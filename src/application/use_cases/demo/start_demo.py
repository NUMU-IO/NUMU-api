"""Provision a fresh demo tenant for the Try-a-Demo flow."""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.demo.seed_demo_tenant import SeedDemoTenantUseCase
from src.core.entities.store import Store, StoreStatus
from src.core.entities.user import User, UserRole, UserStatus
from src.core.exceptions import EntityAlreadyExistsError
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService
from src.core.value_objects.email import Email
from src.core.value_objects.money import Currency
from src.infrastructure.database.models.public.tenant import TenantLifecycleState
from src.infrastructure.repositories.user_repository import UserRepository
from src.infrastructure.tenancy.service import DEMO_LIFETIME_DAYS, TenantService

logger = logging.getLogger(__name__)

DEMO_SUBDOMAIN_PREFIX = "demo-"
DEMO_INTERNAL_EMAIL_DOMAIN = "demo.numu.local"


@dataclass
class DemoCreationResult:
    tenant: object  # TenantModel
    user: User
    store_id: UUID
    access_token: str
    refresh_token: str
    expires_at: datetime
    storefront_url: str
    dashboard_url: str


class StartDemoUseCase:
    """Provision an end-to-end demo tenant from a single email click."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_service: TenantService,
        store_repository: IStoreRepository,
        password_service: IPasswordService,
        token_service: ITokenService,
        seed_use_case: SeedDemoTenantUseCase,
        onboarding_repository=None,
        base_domain: str = "numu.io",
        dashboard_base_url: str = "https://merchant.numueg.app",
    ) -> None:
        self.db = db
        self.tenant_service = tenant_service
        self.store_repository = store_repository
        self.password_service = password_service
        self.token_service = token_service
        self.seed_use_case = seed_use_case
        self.onboarding_repository = onboarding_repository
        self.base_domain = base_domain
        self.dashboard_base_url = dashboard_base_url

    async def execute(
        self, captured_email: str, language: str = "ar", niche: str = "fashion"
    ) -> DemoCreationResult:
        logger.info(
            "demo_start_attempt", extra={"email_hash": _hash_email(captured_email)}
        )

        # 1. Generate unique demo subdomain
        subdomain = await self._generate_unique_subdomain()

        # 2. Create ephemeral demo user
        user = await self._create_demo_user(subdomain)

        # 3. Create demo tenant
        now = datetime.now(UTC)
        tenant = await self.tenant_service.create_tenant(
            name="متجري التجريبي" if language == "ar" else "My Demo Store",
            subdomain=subdomain,
            owner_id=user.id,
            plan="demo",
            is_active=True,
            lifecycle_state=TenantLifecycleState.DEMO,
            expires_at=now + timedelta(days=DEMO_LIFETIME_DAYS),
            demo_email=captured_email,
            demo_started_at=now,
        )

        # 4. Create demo store
        store = await self._create_demo_store(tenant.id, user.id, subdomain, language)

        # 5. Auto-initialize onboarding
        if self.onboarding_repository:
            try:
                from src.application.use_cases.onboarding.auto_complete import (
                    init_onboarding_for_store,
                )

                await init_onboarding_for_store(self.onboarding_repository, store.id)
            except Exception:
                logger.warning("demo_onboarding_init_failed", exc_info=True)

        # 6. Seed sample data
        try:
            await self.seed_use_case.execute(
                tenant_id=tenant.id, store_id=store.id, niche=niche
            )
        except Exception:
            logger.warning("demo_seed_failed", exc_info=True)

        # 7. Issue tenant-scoped tokens
        access_token = self.token_service.create_access_token(user, tenant_id=tenant.id)
        refresh_token = self.token_service.create_refresh_token(
            user, tenant_id=tenant.id
        )

        await self.db.commit()

        logger.info(
            "demo_started", extra={"tenant_id": str(tenant.id), "subdomain": subdomain}
        )

        return DemoCreationResult(
            tenant=tenant,
            user=user,
            store_id=store.id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=tenant.expires_at,
            storefront_url=f"https://{subdomain}.{self.base_domain}",
            dashboard_url=f"{self.dashboard_base_url}/?welcome=demo",
        )

    async def _generate_unique_subdomain(self, max_attempts: int = 5) -> str:
        for _attempt in range(max_attempts):
            subdomain = f"{DEMO_SUBDOMAIN_PREFIX}{secrets.token_hex(4)}"
            existing = await self.tenant_service.tenant_repo.get_by_subdomain(subdomain)
            if not existing:
                return subdomain
        raise RuntimeError(
            f"Failed to generate unique demo subdomain after {max_attempts} attempts"
        )

    async def _create_demo_user(self, subdomain: str) -> User:
        user_repo = UserRepository(self.db)
        local_part = subdomain.replace(DEMO_SUBDOMAIN_PREFIX, "demo-")
        internal_email = f"{local_part}@{DEMO_INTERNAL_EMAIL_DOMAIN}"

        if await user_repo.email_exists(Email(value=internal_email)):
            raise EntityAlreadyExistsError("User", "email", internal_email)

        user = User(
            email=Email(value=internal_email),
            hashed_password=self.password_service.hash_password(
                secrets.token_urlsafe(32)
            ),
            first_name="Demo",
            last_name="Merchant",
            role=UserRole.STORE_OWNER,
            status=UserStatus.ACTIVE,
            email_verified_at=datetime.now(UTC),
        )
        return await user_repo.create(user)

    async def _create_demo_store(
        self, tenant_id: UUID, owner_id: UUID, subdomain: str, language: str
    ) -> Store:
        store = Store(
            name="متجري التجريبي" if language == "ar" else "My Demo Store",
            slug=subdomain,
            subdomain=subdomain,
            owner_id=owner_id,
            description="A demo store created by the Try-a-Demo flow.",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.EGP,
            default_language=language,
            contact_email=None,
            contact_phone=None,
            theme_settings={
                "primaryColor": "#0075FF",
                "secondaryColor": "#1a1a2e",
                "fontFamily": "Inter, sans-serif",
                "logoPosition": "center",
                "showSocialLinks": True,
                "showWhatsAppButton": True,
                "bannerEnabled": True,
                "gridColumns": 3,
                "productCardStyle": "modern",
            },
            tenant_id=tenant_id,
        )
        return await self.store_repository.create(store)


def _hash_email(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]

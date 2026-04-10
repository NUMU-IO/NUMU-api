"""Promote a demo tenant in-place to a real account on a 30-day trial.

Stream 1.2 of the NUMU plan. Called by ``POST /api/v1/demo/convert``.

Steps:
1. Validate input (email not taken, subdomain valid + available).
2. Create a real User row with email + password (pending verification).
3. Wipe demo-seeded fake data (rows tagged with ``metadata.demo_seeded``).
4. Call ``StartTrialUseCase`` to flip tenant lifecycle from demo → trial.
5. Update tenant subdomain + name + owner.
6. Update store subdomain + slug + owner.
7. Delete the ephemeral demo user.
8. Issue fresh tokens for the new real user.
9. Send welcome + verify-email notification.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.billing.start_trial import StartTrialUseCase
from src.application.use_cases.stores.create_store import (
    validate_subdomain,
)
from src.core.entities.user import User, UserRole, UserStatus
from src.core.exceptions import EntityAlreadyExistsError, ValidationError
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService
from src.core.validators.password import validate_password
from src.core.value_objects.email import Email
from src.infrastructure.database.models import StoreModel, UserModel
from src.infrastructure.database.models.public.tenant import TenantLifecycleState
from src.infrastructure.repositories.user_repository import UserRepository
from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)


@dataclass
class ConvertDemoResult:
    tenant_id: UUID
    user: User
    access_token: str
    refresh_token: str
    subdomain: str


class ConvertDemoUseCase:
    """Promote a demo tenant in-place to a real 30-day trial account."""

    def __init__(
        self,
        db: AsyncSession,
        password_service: IPasswordService,
        token_service: ITokenService,
        email_service=None,
    ) -> None:
        self.db = db
        self.password_service = password_service
        self.token_service = token_service
        self.email_service = email_service

    async def execute(
        self,
        demo_user_id: UUID,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        store_name: str,
        subdomain: str,
        phone: str | None = None,
    ) -> ConvertDemoResult:
        log = (
            logger.bind(demo_user_id=str(demo_user_id))
            if hasattr(logger, "bind")
            else logger
        )
        log.info("demo_convert_attempt")

        user_repo = UserRepository(self.db)
        tenant_repo = TenantRepository(self.db)

        # ─── 1. Resolve the demo user + tenant ────────────────────────
        demo_user = await user_repo.get_by_id(demo_user_id)
        if not demo_user:
            raise ValidationError("Demo session not found.", field="user")

        # Find the tenant owned by this demo user
        from src.infrastructure.database.models.public.tenant import TenantModel

        tenant_q = select(TenantModel).where(TenantModel.owner_id == demo_user_id)
        tenant = (await self.db.execute(tenant_q)).scalar_one_or_none()
        if not tenant or tenant.lifecycle_state != TenantLifecycleState.DEMO:
            raise ValidationError("Not a demo tenant.", field="tenant")

        # ─── 2. Validate new email not taken ──────────────────────────
        new_email = Email(value=email)
        if await user_repo.email_exists(new_email):
            raise EntityAlreadyExistsError("User", "email", email)

        # ─── 3. Validate + normalize subdomain ────────────────────────
        subdomain = validate_subdomain(subdomain)
        # Check subdomain not already used by another tenant/store
        existing_tenant = await tenant_repo.get_by_subdomain(subdomain)
        if existing_tenant and existing_tenant.id != tenant.id:
            raise ValidationError(
                f"Subdomain '{subdomain}' is already taken.", field="subdomain"
            )

        # ─── 4. Validate password ─────────────────────────────────────
        validate_password(password)

        # ─── 5. Create new real user ──────────────────────────────────
        hashed_password = self.password_service.hash_password(password)
        new_user = User(
            email=new_email,
            hashed_password=hashed_password,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.STORE_OWNER,
            status=UserStatus.PENDING_VERIFICATION,
            phone=phone,
            trial_ends_at=datetime.now(UTC) + timedelta(days=30),
        )
        created_user = await user_repo.create(new_user)

        # ─── 6. Wipe demo-seeded data (by metadata tag) ──────────────
        await self._wipe_demo_seeded_data(tenant.id)

        # ─── 7. Flip tenant to trial via StartTrialUseCase ────────────
        start_trial = StartTrialUseCase(tenant_repo)
        tenant = await start_trial.execute(tenant)

        # ─── 8. Update tenant identity ────────────────────────────────
        tenant.name = store_name
        tenant.subdomain = subdomain
        tenant.owner_id = created_user.id
        tenant.demo_email = None
        tenant.demo_started_at = None
        await tenant_repo.update(tenant)

        # ─── 9. Update store subdomain + owner ────────────────────────
        store_q = select(StoreModel).where(StoreModel.tenant_id == tenant.id)
        store = (await self.db.execute(store_q)).scalar_one_or_none()
        if store:
            store.name = store_name
            store.subdomain = subdomain
            store.slug = subdomain
            store.owner_id = created_user.id
            self.db.add(store)

        # ─── 10. Delete ephemeral demo user ───────────────────────────
        demo_user_model_q = select(UserModel).where(UserModel.id == demo_user_id)
        demo_user_model = (
            await self.db.execute(demo_user_model_q)
        ).scalar_one_or_none()
        if demo_user_model:
            await self.db.delete(demo_user_model)

        # ─── 11. Issue fresh tokens for the real user ─────────────────
        access_token = self.token_service.create_access_token(
            created_user, tenant_id=tenant.id
        )
        refresh_token = self.token_service.create_refresh_token(
            created_user, tenant_id=tenant.id
        )

        await self.db.commit()

        # ─── 12. Send welcome + verification email (best-effort) ──────
        if self.email_service:
            try:
                verification_token = self.token_service.create_email_verification_token(
                    created_user
                )
                await self.email_service.send_verification_email(
                    email=email, token=verification_token, code=None
                )
            except Exception:
                logger.warning("demo_convert_verification_email_failed", exc_info=True)

        logger.info(
            "demo_converted",
            extra={
                "tenant_id": str(tenant.id),
                "new_user_id": str(created_user.id),
                "subdomain": subdomain,
            },
        )

        return ConvertDemoResult(
            tenant_id=tenant.id,
            user=created_user,
            access_token=access_token,
            refresh_token=refresh_token,
            subdomain=subdomain,
        )

    async def _wipe_demo_seeded_data(self, tenant_id: UUID) -> None:
        """Delete rows that carry the ``demo_seeded`` metadata tag.

        This preserves anything the user added themselves during the demo.
        Currently a no-op since the v1 seed stub doesn't create rows yet —
        when Stream 1.3 fills the seeder, this method will delete products,
        categories, customers, and orders with metadata->>'demo_seeded' = 'true'.
        """
        # TODO (Stream 1.3): uncomment once seed creates tagged rows
        # from src.infrastructure.database.models import ProductModel, CategoryModel, OrderModel, CustomerModel
        # for model in [OrderModel, ProductModel, CategoryModel, CustomerModel]:
        #     await self.db.execute(
        #         delete(model).where(
        #             model.tenant_id == tenant_id,
        #             model.metadata["demo_seeded"].astext == "true",
        #         )
        #     )
        pass

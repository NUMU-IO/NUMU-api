"""Create store use case."""

import re
import uuid
from uuid import UUID

from slugify import slugify

from src.application.dto.store import CreateStoreDTO, StoreDTO
from src.core.entities.store import Store, StoreStatus
from src.core.exceptions import EntityAlreadyExistsError, ValidationError
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.value_objects.money import Currency
from src.infrastructure.tenancy.service import TenantService

# Reserved subdomains that cannot be used
RESERVED_SUBDOMAINS = {
    "www",
    "api",
    "admin",
    "dashboard",
    "app",
    "mail",
    "email",
    "ftp",
    "ssh",
    "sftp",
    "cpanel",
    "webmail",
    "ns1",
    "ns2",
    "shop",
    "store",
    "checkout",
    "pay",
    "payment",
    "billing",
    "support",
    "help",
    "docs",
    "blog",
    "cdn",
    "static",
    "assets",
    "test",
    "staging",
    "dev",
    "demo",
    "beta",
    "alpha",
    "numu",
    "numo",
    "numa",
}


def validate_subdomain(subdomain: str) -> str:
    """Validate and normalize subdomain.

    Args:
        subdomain: The subdomain to validate

    Returns:
        Normalized subdomain (lowercase)

    Raises:
        ValidationError: If subdomain is invalid
    """
    # Normalize
    subdomain = subdomain.lower().strip()

    # Check length (3-63 characters per DNS rules)
    if len(subdomain) < 3:
        raise ValidationError(
            "Subdomain must be at least 3 characters", field="subdomain"
        )
    if len(subdomain) > 63:
        raise ValidationError(
            "Subdomain must be at most 63 characters", field="subdomain"
        )

    # Check format (letters, numbers, hyphens only, can't start/end with hyphen)
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", subdomain) and len(subdomain) > 2:
        raise ValidationError(
            "Subdomain can only contain letters, numbers, and hyphens, "
            "and cannot start or end with a hyphen",
            field="subdomain",
        )

    # Check for consecutive hyphens
    if "--" in subdomain:
        raise ValidationError(
            "Subdomain cannot contain consecutive hyphens", field="subdomain"
        )

    # Check reserved subdomains
    if subdomain in RESERVED_SUBDOMAINS:
        raise ValidationError(
            f"'{subdomain}' is a reserved subdomain", field="subdomain"
        )

    return subdomain


class CreateStoreUseCase:
    """Use case for creating a new store.

    Beta-invite redemption is handled by the dedicated /public/beta/redeem
    endpoint, which combines user signup with store creation atomically.
    This use case is the post-signup path: it just creates a store for an
    already-authenticated owner.
    """

    def __init__(
        self,
        store_repository: IStoreRepository,
        tenant_service: TenantService,
        onboarding_repository: IOnboardingRepository | None = None,
    ) -> None:
        self.store_repository = store_repository
        self.tenant_service = tenant_service
        self.onboarding_repository = onboarding_repository

    async def execute(
        self,
        dto: CreateStoreDTO,
        owner_id: UUID,
        plan: str = "free",
    ) -> StoreDTO:
        """Create a new store for the given owner."""
        # Validate and normalize subdomain
        subdomain = validate_subdomain(dto.subdomain)

        # Check if subdomain already exists
        if await self.store_repository.subdomain_exists(subdomain):
            raise EntityAlreadyExistsError("Store", "subdomain", subdomain)

        # Generate slug if not provided (use subdomain as base)
        slug = dto.slug or slugify(dto.name)

        # Check if slug already exists
        if await self.store_repository.slug_exists(slug):
            # Append a random suffix to make it unique
            slug = f"{slug}-{str(uuid.uuid4())[:8]}"

        # Parse currency
        try:
            currency = Currency(dto.default_currency)
        except ValueError:
            currency = Currency.EGP  # Default to EGP for Egyptian market

        # Default theme settings for NUMU-shop
        default_theme_settings = {
            "primaryColor": "#0075FF",
            "secondaryColor": "#1a1a2e",
            "fontFamily": "Inter, sans-serif",
            "logoPosition": "left",
            "showSocialLinks": True,
            "showWhatsAppButton": True,
            "bannerEnabled": True,
            "gridColumns": 3,
            "productCardStyle": "modern",
        }

        # Seed canonical WhatsApp notification toggles so the order-lifecycle
        # handlers find an explicit boolean per key (instead of falling
        # through to the dict.get(..., True) default which is fragile and
        # masks intent). New platform-managed stores get all 4 order-related
        # toggles ON for the seamless out-of-box experience; abandoned_cart
        # stays OFF until the scheduled-send dispatcher (US3) ships.
        # connect_byo_credentials.py resets the same keys to all-False
        # when the merchant later opts into BYO mode (per FR-019a).
        default_settings = {
            "whatsapp_notifications": {
                "order_confirmation": True,
                "payment_received": True,
                "shipping_update": True,
                "delivery_confirmation": True,
                "abandoned_cart": False,
            },
        }

        # tenant.is_active gates TenantMiddleware routing — never leave a
        # brand-new store's tenant inactive, or the owner 404s on their own dashboard.
        tenant = await self.tenant_service.create_tenant(
            name=dto.name,
            subdomain=subdomain,
            owner_id=owner_id,
            plan=plan,
            is_active=True,
        )

        store = Store(
            name=dto.name,
            slug=slug,
            subdomain=subdomain,
            owner_id=owner_id,
            description=dto.description,
            status=StoreStatus.ACTIVE,
            default_currency=currency,
            default_language=dto.default_language,
            contact_email=dto.contact_email,
            contact_phone=dto.contact_phone,
            theme_settings=default_theme_settings,
            settings=default_settings,
            tenant_id=tenant.id,
        )

        # Save store
        created_store = await self.store_repository.create(store)

        # Initialize onboarding with create_store step already completed
        if self.onboarding_repository:
            from src.application.use_cases.onboarding.auto_complete import (
                init_onboarding_for_store,
            )

            await init_onboarding_for_store(
                self.onboarding_repository, created_store.id
            )

        return StoreDTO.from_entity(created_store)

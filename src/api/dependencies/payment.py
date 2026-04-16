"""Tenant-aware payment service factory.

Loads per-merchant credentials from the database and constructs
payment services with those credentials. Falls back to environment
variable credentials if no per-tenant credentials are configured.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.services import get_payment_service_for_provider
from src.config.logging_config import get_logger
from src.core.interfaces.services.payment_service import IPaymentService
from src.infrastructure.database.models.tenant.configuration import (
    ServiceName,
    ServiceType,
)
from src.infrastructure.repositories.credential_repository import (
    CredentialRepository,
)

logger = get_logger(__name__)

# Maps provider string to (ServiceType, ServiceName) for DB lookup
PROVIDER_SERVICE_MAP: dict[str, tuple[ServiceType, ServiceName]] = {
    "kashier": (ServiceType.PAYMENT_GATEWAY, ServiceName.KASHIER),
    "paymob": (ServiceType.PAYMENT_GATEWAY, ServiceName.PAYMOB),
    "fawry": (ServiceType.PAYMENT_GATEWAY, ServiceName.FAWRY),
    "stripe": (ServiceType.PAYMENT_GATEWAY, ServiceName.STRIPE),
    "fawaterak": (ServiceType.PAYMENT_GATEWAY, ServiceName.FAWATERAK),
}


def _build_kashier(creds: dict[str, Any]):
    """Build KashierPaymentService from decrypted credentials."""
    from src.infrastructure.external_services.kashier import KashierPaymentService

    return KashierPaymentService(
        mid=creds["mid"],
        api_key=creds["api_key"],
        mode=creds.get("mode"),
    )


def _build_paymob(creds: dict[str, Any]):
    """Build PaymobPaymentService from decrypted credentials."""
    from src.infrastructure.external_services.paymob import PaymobPaymentService

    return PaymobPaymentService(
        api_key=creds.get("api_key"),
        integration_id=creds.get("integration_id"),
        iframe_id=creds.get("iframe_id"),
        hmac_secret=creds.get("hmac_secret"),
        wallet_integration_id=creds.get("wallet_integration_id"),
    )


def _build_fawaterak(creds: dict[str, Any]):
    """Build FawaterakPaymentService from decrypted credentials."""
    from src.infrastructure.external_services.fawaterak import FawaterakPaymentService

    return FawaterakPaymentService(
        api_key=creds.get("api_key"),
        vendor_key=creds.get("vendor_key"),
        environment=creds.get("environment", "staging"),
    )


def _build_fawry(creds: dict[str, Any]):
    """Build FawryPaymentService from decrypted credentials."""
    from src.infrastructure.external_services.fawry import FawryPaymentService

    return FawryPaymentService(
        merchant_code=creds.get("merchant_code"),
        security_key=creds.get("security_key"),
    )


# Maps provider string to a builder function
PROVIDER_BUILDERS: dict[str, Any] = {
    "kashier": _build_kashier,
    "paymob": _build_paymob,
    "fawry": _build_fawry,
    "fawaterak": _build_fawaterak,
}


async def get_tenant_payment_service(
    provider: str,
    tenant_id: UUID,
    session: AsyncSession,
) -> IPaymentService:
    """Load per-tenant payment service with merchant-specific credentials.

    Looks up encrypted credentials in the database for the given tenant
    and provider, decrypts them, and constructs the appropriate payment
    service. Falls back to environment variable credentials if no
    per-tenant credentials are found.

    Args:
        provider: Payment provider string (e.g., "kashier", "paymob").
        tenant_id: The tenant/merchant UUID.
        session: Database session for credential lookup.

    Returns:
        An IPaymentService instance configured with the correct credentials.
    """
    service_mapping = PROVIDER_SERVICE_MAP.get(provider)
    builder = PROVIDER_BUILDERS.get(provider)

    if service_mapping and builder:
        service_type, service_name = service_mapping
        cred_repo = CredentialRepository(session)
        creds = await cred_repo.get_decrypted_credentials(
            tenant_id=tenant_id,
            service_type=service_type,
            service_name=service_name,
        )

        if creds:
            logger.info(
                "payment_service_using_tenant_credentials",
                provider=provider,
                tenant_id=str(tenant_id),
            )
            return builder(creds)

    # Fall back to environment variable credentials
    logger.info(
        "payment_service_using_env_credentials",
        provider=provider,
        tenant_id=str(tenant_id),
    )
    return get_payment_service_for_provider(provider)

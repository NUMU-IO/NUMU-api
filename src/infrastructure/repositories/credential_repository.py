"""Repository for loading and decrypting per-tenant service credentials."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.infrastructure.database.models.tenant.configuration import (
    ServiceCredential,
    ServiceName,
    ServiceType,
)

logger = get_logger(__name__)


class CredentialRepository:
    """Repository for querying and decrypting tenant service credentials.

    Used by payment services to load per-merchant credentials at
    checkout and webhook processing time.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_decrypted_credentials(
        self,
        tenant_id: UUID,
        service_type: ServiceType,
        service_name: ServiceName,
    ) -> dict[str, Any] | None:
        """Load and decrypt credentials for a tenant's service.

        Args:
            tenant_id: The tenant/merchant UUID.
            service_type: e.g. ServiceType.PAYMENT_GATEWAY
            service_name: e.g. ServiceName.KASHIER

        Returns:
            Decrypted credential dict, or None if not found/inactive/error.
        """
        query = (
            select(ServiceCredential)
            .where(ServiceCredential.tenant_id == tenant_id)
            .where(ServiceCredential.service_type == service_type)
            .where(ServiceCredential.service_name == service_name)
            .where(ServiceCredential.is_active.is_(True))
        )
        result = await self.session.execute(query)
        credential = result.scalar_one_or_none()

        if not credential:
            return None

        try:
            from src.infrastructure.external_services.secrets import (
                get_secrets_manager,
            )

            secrets_manager = get_secrets_manager()
            decrypted = await secrets_manager.decrypt(
                credential.credentials_encrypted,
                credential.encryption_key_id,
            )
            return decrypted
        except Exception as e:
            logger.warning(
                "credential_decryption_failed",
                tenant_id=str(tenant_id),
                service_name=service_name.value,
                error=str(e),
            )
            return None

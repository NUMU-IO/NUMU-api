"""WhatsApp Business API messaging service."""

import logging
from typing import Any
from uuid import UUID

from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)

logger = logging.getLogger(__name__)

__all__ = ["WhatsAppMessagingService", "get_whatsapp_service"]


async def get_whatsapp_service(
    store_id: UUID,
    db_session: Any,
    tenant_id: UUID | None = None,
) -> WhatsAppMessagingService:
    """Resolve a WhatsApp service with per-store credentials if available.

    Looks up encrypted credentials in ServiceCredential for this store's
    tenant. If found and active, creates a service with per-store tokens.
    Otherwise, falls back to global NUMU credentials from settings.

    Args:
        store_id: The store UUID.
        db_session: AsyncSession for credential lookup.
        tenant_id: Optional tenant UUID (if not provided, derived from store).

    Returns:
        WhatsAppMessagingService configured with the correct credentials.
    """
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.repositories.credential_repository import (
        CredentialRepository,
    )

    if not tenant_id:
        # Resolve tenant_id from store
        from sqlalchemy import select

        from src.infrastructure.database.models.tenant.store import StoreModel

        result = await db_session.execute(
            select(StoreModel.tenant_id).where(StoreModel.id == store_id)
        )
        row = result.scalar_one_or_none()
        if row:
            tenant_id = row

    if tenant_id:
        cred_repo = CredentialRepository(db_session)
        creds = await cred_repo.get_decrypted_credentials(
            tenant_id=tenant_id,
            service_type=ServiceType.WHATSAPP,
            service_name=ServiceName.WHATSAPP_BUSINESS,
        )

        if creds:
            logger.info(
                "whatsapp_service_using_store_credentials",
                extra={"store_id": str(store_id)},
            )
            service = WhatsAppMessagingService(
                access_token=creds.get("access_token"),
                phone_number_id=creds.get("phone_number_id"),
                business_account_id=creds.get("waba_id"),
                app_secret=creds.get("app_secret"),
            )
            service._is_own = True
            return service

    # Fall back to global NUMU credentials
    logger.debug(
        "whatsapp_service_using_global_credentials",
        extra={"store_id": str(store_id)},
    )
    service = WhatsAppMessagingService()
    service._is_own = False
    return service

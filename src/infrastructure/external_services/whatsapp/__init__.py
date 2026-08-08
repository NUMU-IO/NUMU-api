"""WhatsApp messaging services and the per-store transport resolver."""

import logging
from typing import Any
from uuid import UUID

from src.infrastructure.external_services.whatsapp.gowa_provider import GowaProvider
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GowaProvider",
    "WhatsAppMessagingService",
    "get_whatsapp_service",
    "resolve_provider_name",
]

# Store setting that selects the transport. Meta Cloud is the default and
# stays the default: GOWA is unofficial and puts the sending number at risk of
# a ban, so a store only moves onto it when someone deliberately switches it in
# the admin backoffice.
_PROVIDER_SETTING_PATH = ("whatsapp", "provider")
_PROVIDER_META = "meta_cloud"
_PROVIDER_GOWA = "gowa"


def resolve_provider_name(store_settings: dict | None) -> str:
    """Read the configured transport out of a store's settings JSON.

    Anything unrecognised resolves to Meta. A typo in the settings blob must
    not silently route a merchant's messages over the unofficial transport.
    """
    node: Any = store_settings or {}
    for key in _PROVIDER_SETTING_PATH:
        if not isinstance(node, dict):
            return _PROVIDER_META
        node = node.get(key)
    return _PROVIDER_GOWA if node == _PROVIDER_GOWA else _PROVIDER_META


async def get_whatsapp_service(
    store_id: UUID,
    db_session: Any,
    tenant_id: UUID | None = None,
) -> "WhatsAppMessagingService | GowaProvider":
    """Resolve the WhatsApp transport this store should send through.

    Two axes, resolved in this order:

    1. **Which transport.** ``settings.whatsapp.provider`` picks between Meta
       Cloud (default) and GOWA. GOWA additionally needs an active paired
       device; if the store asked for GOWA but has none, we log and fall
       through rather than send from the wrong account.
    2. **Whose credentials.** For Meta, per-tenant ``ServiceCredential`` rows
       (BYO) win over the platform's global token. For GOWA the equivalent is
       whether the paired device is the merchant's own number.

    Both transports expose the same sending surface, so callers do not care
    which one comes back.

    Args:
        store_id: The store UUID.
        db_session: AsyncSession for the settings / credential / device lookups.
        tenant_id: Optional tenant UUID (derived from the store when omitted).

    Returns:
        A configured transport for this store.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.configuration import (
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.repositories.credential_repository import (
        CredentialRepository,
    )

    # One read for both the tenant and the transport choice — the settings blob
    # is on the same row we already needed.
    result = await db_session.execute(
        select(StoreModel.tenant_id, StoreModel.settings).where(
            StoreModel.id == store_id
        )
    )
    store_row = result.first()
    if store_row:
        if not tenant_id:
            tenant_id = store_row[0]
        store_settings = store_row[1]
    else:
        store_settings = None

    # ── GOWA transport ──────────────────────────────────────────────────────
    # Selected per store, never by default. Requires an ACTIVE paired device:
    # without one there is no WhatsApp account to send as, and falling back to
    # Meta silently would be worse than failing — the merchant switched
    # deliberately and needs to see that pairing is broken.
    if resolve_provider_name(store_settings) == _PROVIDER_GOWA:
        from src.infrastructure.repositories.whatsapp_gowa_device_repository import (
            WhatsAppGowaDeviceRepository,
        )

        device = await WhatsAppGowaDeviceRepository(db_session).get_active_for_store(
            store_id
        )
        if device:
            logger.info(
                "whatsapp_service_using_gowa",
                extra={
                    "store_id": str(store_id),
                    "device_id": device.device_id,
                    "device_status": device.status,
                },
            )
            # `is_own` mirrors the Meta vocabulary: a merchant-paired number is
            # "own", the platform's shared number is not.
            return GowaProvider(
                device_id=device.device_id,
                is_own=device.phone is not None,
                # Numbered prompts must record what each digit means before
                # they go out; the transport needs a session to do that.
                db_session=db_session,
                store_id=store_id,
                tenant_id=tenant_id,
            )
        logger.warning(
            "whatsapp_gowa_selected_but_unpaired",
            extra={"store_id": str(store_id)},
        )

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

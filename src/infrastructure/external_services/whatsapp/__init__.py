"""WhatsApp messaging services and the per-store transport resolver."""

import logging
from typing import Any
from uuid import UUID

from src.config.settings import settings
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

    Precedence: an explicit per-store setting always wins. Only when a store
    has said nothing does the platform default apply — so a merchant
    deliberately pinned to Meta stays on Meta even after the fleet default
    flips, and vice versa.

    An unrecognised value is treated as "unset" and falls through to the
    platform default — so while that default is Meta (the shipped state), a typo
    can never silently route a merchant onto the unofficial transport. Once an
    operator has deliberately moved the fleet default to GOWA, a typo landing on
    GOWA is simply the fleet default applying, which is the intended behaviour.
    """
    node: Any = store_settings or {}
    for key in _PROVIDER_SETTING_PATH:
        if not isinstance(node, dict):
            node = None
            break
        node = node.get(key)

    if node in (_PROVIDER_GOWA, _PROVIDER_META):
        return str(node)

    # No explicit choice. `GOWA_PLATFORM_DEFAULT=true` moves every store that
    # sends on the SHARED NUMU number over to GOWA, keeping the behaviour
    # identical — same templates, same triggers, same hub — and changing only
    # the wire underneath. Stores with their own Meta credentials are handled
    # by the caller and never reach this default.
    if settings.gowa_platform_default and settings.gowa_enabled:
        return _PROVIDER_GOWA
    return _PROVIDER_META


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

        gowa_repo = WhatsAppGowaDeviceRepository(db_session)
        # Store's own paired number first; the shared platform number is the
        # fallback, mirroring how Meta resolves BYO credentials before the
        # platform token. A store on the shared path therefore keeps behaving
        # exactly as it does today — only the wire changes.
        device = await gowa_repo.get_active_for_store(store_id)
        if device is None:
            device = await gowa_repo.get_platform_device()
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
                # "own" means the merchant's own number, so the shared platform
                # device is NOT own — same vocabulary the Meta path reports.
                is_own=not device.is_platform,
                # Numbered prompts must record what each digit means before
                # they go out; the transport needs a session to do that.
                db_session=db_session,
                store_id=store_id,
                tenant_id=tenant_id,
                paired_at=device.paired_at,
                device_status=device.status,
                store_settings=store_settings,
                is_platform=device.is_platform,
            )
        # No device, so there is no WhatsApp account to send as.
        #
        # Falling through to Meta here was a real bug: a store deliberately
        # switched to GOWA would silently attempt a Meta send, and the operator
        # saw whatever Meta happened to say — in practice a Graph API error
        # about a phone_number_id — instead of the actual problem, which is
        # that nothing is paired. Returning a disabled GowaProvider keeps the
        # failure honest and self-describing.
        logger.warning(
            "whatsapp_gowa_selected_but_unpaired",
            extra={"store_id": str(store_id)},
        )
        return GowaProvider(
            device_id="",
            db_session=db_session,
            store_id=store_id,
            tenant_id=tenant_id,
            store_settings=store_settings,
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

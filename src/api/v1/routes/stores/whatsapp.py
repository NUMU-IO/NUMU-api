"""WhatsApp Business API routes — signup, status, disconnect, analytics, notifications."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_store, get_store_repository
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.stores.whatsapp import (
    ConnectionType,
    EmbeddedSignupConfig,
    EmbeddedSignupRequest,
    EmbeddedSignupResponse,
    NotificationSettings,
    NotificationToggle,
    UpdateNotificationSettingsRequest,
    WhatsAppAnalytics,
    WhatsAppConnectionStatus,
    WhatsAppDayStat,
)
from src.api.v1.schemas.stores.whatsapp_connection import (
    BYOConnectRequest,
    BYOValidationFailure,
    WhatsAppStatus,
)
from src.config import settings
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.configuration import (
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.database.models.tenant.message_log import MessageLogModel
from src.infrastructure.database.models.tenant.whatsapp_conversation import (
    WhatsAppConversationModel,
)
from src.infrastructure.repositories import StoreRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{store_id}/whatsapp")

GRAPH_API_BASE = "https://graph.facebook.com/v18.0"


# ── Embedded Signup ──


@router.get(
    "/signup-config",
    response_model=SuccessResponse[EmbeddedSignupConfig],
    summary="Get Meta Embedded Signup config",
    operation_id="get_whatsapp_signup_config",
)
async def get_signup_config(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Return the Meta App ID and config needed for the embedded signup JS SDK."""
    return SuccessResponse(
        data=EmbeddedSignupConfig(
            app_id=settings.meta_app_id or "",
            config_id=settings.meta_config_id or "",
            enabled=bool(settings.meta_app_id),
        ),
        message="Signup config retrieved",
    )


@router.post(
    "/complete-signup",
    response_model=SuccessResponse[EmbeddedSignupResponse],
    summary="Complete WhatsApp embedded signup",
    operation_id="complete_whatsapp_signup",
)
async def complete_signup(
    request: EmbeddedSignupRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    """Exchange the code from Meta's embedded signup for permanent credentials.

    1. Exchange code for short-lived user token
    2. Exchange for system user token
    3. Get WABA ID and phone number ID from debug_token
    4. Subscribe to webhooks
    5. Store encrypted credentials
    """
    try:
        async with httpx.AsyncClient() as client:
            # Step 1: Exchange code for user access token
            token_resp = await client.get(
                f"{GRAPH_API_BASE}/oauth/access_token",
                params={
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "code": request.code,
                },
                timeout=30.0,
            )
            if token_resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Token exchange failed: {token_resp.text[:200]}",
                )
            token_data = token_resp.json()
            access_token = token_data["access_token"]

            # Step 2: Debug token to get WABA and phone info
            debug_resp = await client.get(
                f"{GRAPH_API_BASE}/debug_token",
                params={
                    "input_token": access_token,
                    "access_token": f"{settings.meta_app_id}|{settings.meta_app_secret}",
                },
                timeout=30.0,
            )
            debug_data = debug_resp.json().get("data", {})
            granular_scopes = debug_data.get("granular_scopes", [])

            # Extract WABA ID from scopes
            waba_id = None
            phone_number_id = None
            for scope in granular_scopes:
                if scope.get("scope") == "whatsapp_business_management":
                    waba_ids = scope.get("target_ids", [])
                    if waba_ids:
                        waba_id = waba_ids[0]
                elif scope.get("scope") == "whatsapp_business_messaging":
                    phone_ids = scope.get("target_ids", [])
                    if phone_ids:
                        phone_number_id = phone_ids[0]

            if not waba_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Could not determine WhatsApp Business Account ID from signup",
                )

            # Step 3: If no phone_number_id from scopes, fetch from WABA
            display_name = None
            phone_number = None
            if not phone_number_id:
                phones_resp = await client.get(
                    f"{GRAPH_API_BASE}/{waba_id}/phone_numbers",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=30.0,
                )
                if phones_resp.status_code == 200:
                    phones = phones_resp.json().get("data", [])
                    if phones:
                        phone_number_id = phones[0].get("id")
                        display_name = phones[0].get("verified_name")
                        phone_number = phones[0].get("display_phone_number")

            # Step 4: Subscribe WABA to our app's webhooks
            await client.post(
                f"{GRAPH_API_BASE}/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )

            # Step 5: Store credentials encrypted
            from src.infrastructure.external_services.secrets import (
                get_secrets_manager,
            )

            secrets = get_secrets_manager()
            key_id = await secrets.get_current_key_id()
            creds_data = {
                "access_token": access_token,
                "waba_id": str(waba_id),
                "phone_number_id": str(phone_number_id),
                "display_name": display_name,
                "phone_number": phone_number,
            }
            encrypted = await secrets.encrypt(creds_data, key_id)

            # Upsert ServiceCredential
            existing = await db.execute(
                select(ServiceCredential).where(
                    ServiceCredential.tenant_id == store.tenant_id,
                    ServiceCredential.service_type == ServiceType.WHATSAPP,
                    ServiceCredential.service_name == ServiceName.WHATSAPP_BUSINESS,
                )
            )
            cred = existing.scalar_one_or_none()
            if cred:
                cred.credentials_encrypted = encrypted
                cred.encryption_key_id = key_id
                cred.is_active = True
                cred.is_validated = True
                cred.last_validated_at = datetime.now(UTC)
                cred.extra_metadata = {
                    "phone_number": phone_number,
                    "display_name": display_name,
                    "waba_id": str(waba_id),
                }
            else:
                cred = ServiceCredential(
                    tenant_id=store.tenant_id,
                    service_type=ServiceType.WHATSAPP,
                    service_name=ServiceName.WHATSAPP_BUSINESS,
                    credentials_encrypted=encrypted,
                    encryption_key_id=key_id,
                    is_active=True,
                    is_validated=True,
                    last_validated_at=datetime.now(UTC),
                    extra_metadata={
                        "phone_number": phone_number,
                        "display_name": display_name,
                        "waba_id": str(waba_id),
                    },
                )
                db.add(cred)
            await db.flush()

            # Update store whatsapp settings
            store_settings = store.settings or {}
            wa_settings = store_settings.get("whatsapp", {})
            wa_settings["enabled"] = True
            wa_settings["is_configured"] = True
            wa_settings["connection_type"] = "own"
            wa_settings["last_configured"] = datetime.now(UTC).isoformat()
            store_settings["whatsapp"] = wa_settings
            store.settings = store_settings
            store_repo = StoreRepository(db)
            await store_repo.update(store)

        return SuccessResponse(
            data=EmbeddedSignupResponse(
                connected=True,
                phone_number=phone_number,
                display_name=display_name,
                waba_id=str(waba_id),
            ),
            message="WhatsApp Business connected successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("WhatsApp embedded signup failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete WhatsApp signup",
        )


@router.delete(
    "/disconnect",
    response_model=SuccessResponse[dict],
    summary="Disconnect WhatsApp Business",
    operation_id="disconnect_whatsapp",
)
async def disconnect(
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    """Revoke per-store WhatsApp credentials, fall back to shared NUMU number."""
    result = await db.execute(
        select(ServiceCredential).where(
            ServiceCredential.tenant_id == store.tenant_id,
            ServiceCredential.service_type == ServiceType.WHATSAPP,
            ServiceCredential.service_name == ServiceName.WHATSAPP_BUSINESS,
        )
    )
    cred = result.scalar_one_or_none()
    if cred:
        cred.is_active = False
        await db.flush()

    # Update store settings
    store_settings = store.settings or {}
    wa_settings = store_settings.get("whatsapp", {})
    wa_settings["connection_type"] = "shared"
    wa_settings["is_configured"] = bool(settings.whatsapp_enabled)
    store_settings["whatsapp"] = wa_settings
    store.settings = store_settings
    store_repo = StoreRepository(db)
    await store_repo.update(store)

    return SuccessResponse(data={}, message="WhatsApp disconnected")


# ── Status ──


@router.get(
    "/status",
    response_model=SuccessResponse[WhatsAppConnectionStatus],
    summary="Get WhatsApp connection status",
    operation_id="get_whatsapp_status",
)
async def get_status(
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    """Return connection status including phone number and quality rating."""
    result = await db.execute(
        select(ServiceCredential).where(
            ServiceCredential.tenant_id == store.tenant_id,
            ServiceCredential.service_type == ServiceType.WHATSAPP,
            ServiceCredential.service_name == ServiceName.WHATSAPP_BUSINESS,
            ServiceCredential.is_active.is_(True),
        )
    )
    cred = result.scalar_one_or_none()

    if cred and cred.extra_metadata:
        return SuccessResponse(
            data=WhatsAppConnectionStatus(
                connected=True,
                connection_type=ConnectionType.OWN,
                phone_number=cred.extra_metadata.get("phone_number"),
                phone_display_name=cred.extra_metadata.get("display_name"),
                waba_id=cred.extra_metadata.get("waba_id"),
                connected_at=cred.last_validated_at.isoformat()
                if cred.last_validated_at
                else None,
            ),
            message="Connected with own WhatsApp Business number",
        )

    # Shared NUMU number
    connected = bool(settings.whatsapp_enabled and settings.whatsapp_phone_number_id)
    return SuccessResponse(
        data=WhatsAppConnectionStatus(
            connected=connected,
            connection_type=ConnectionType.SHARED,
            phone_number=None,
            phone_display_name="NUMU" if connected else None,
        ),
        message="Using shared NUMU WhatsApp number"
        if connected
        else "WhatsApp not configured",
    )


# ── Notification Settings ──


# Canonical default object — must stay in sync with the seed in
# create_store.py and the backfill migration
# (20260729_010000_backfill_wa_notif_defaults.py). The handler reads
# the same keys via _resolve_send_context, so any drift between the
# three would re-introduce Gap B (toggle-key mismatch).
_NOTIFICATION_DEFAULTS = {
    "order_confirmation": True,
    "payment_received": True,
    "shipping_update": True,
    "delivery_confirmation": True,
    "abandoned_cart": False,
    # Off by default — merchants opt in per store. When True, the
    # OrderCreatedEvent handler sends `order_confirmation_request_v1`
    # (interactive QUICK_REPLY) instead of `order_confirmation_v2`
    # (receipt-style). Surfaces in the dashboard as a per-order
    # confirmation status badge.
    "require_order_confirmation": False,
}


@router.get(
    "/notifications",
    response_model=SuccessResponse[NotificationSettings],
    summary="Get WhatsApp notification settings",
    operation_id="get_whatsapp_notifications",
)
async def get_notification_settings(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get per-notification-type toggles.

    Reads from ``store.settings.whatsapp_notifications`` — the same path
    the backend order-lifecycle handlers consult. The legacy path
    ``store.settings.whatsapp.notification_toggles`` is no longer
    read or written; backfill migration 20260729_010000 seeded the
    canonical path for existing stores, and create_store.py seeds it
    for new stores.
    """
    store_settings = store.settings or {}
    notifs = store_settings.get("whatsapp_notifications", {}) or {}

    return SuccessResponse(
        data=NotificationSettings(
            order_confirmation=NotificationToggle(
                enabled=notifs.get(
                    "order_confirmation", _NOTIFICATION_DEFAULTS["order_confirmation"]
                )
            ),
            payment_received=NotificationToggle(
                enabled=notifs.get(
                    "payment_received", _NOTIFICATION_DEFAULTS["payment_received"]
                )
            ),
            shipping_update=NotificationToggle(
                enabled=notifs.get(
                    "shipping_update", _NOTIFICATION_DEFAULTS["shipping_update"]
                )
            ),
            delivery_confirmation=NotificationToggle(
                enabled=notifs.get(
                    "delivery_confirmation",
                    _NOTIFICATION_DEFAULTS["delivery_confirmation"],
                )
            ),
            abandoned_cart=NotificationToggle(
                enabled=notifs.get(
                    "abandoned_cart", _NOTIFICATION_DEFAULTS["abandoned_cart"]
                )
            ),
            require_order_confirmation=NotificationToggle(
                enabled=notifs.get(
                    "require_order_confirmation",
                    _NOTIFICATION_DEFAULTS["require_order_confirmation"],
                )
            ),
        ),
        message="Notification settings retrieved",
    )


@router.patch(
    "/notifications",
    response_model=SuccessResponse[NotificationSettings],
    summary="Update WhatsApp notification settings",
    operation_id="update_whatsapp_notifications",
)
async def update_notification_settings(
    request: UpdateNotificationSettingsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Toggle individual notification types on/off.

    Writes to ``store.settings.whatsapp_notifications.{key}`` — the same
    path the backend handlers read at send-time. Partial update: only
    keys present (non-None) in the request body are written.
    """
    store_settings = store.settings or {}
    toggles = store_settings.setdefault("whatsapp_notifications", {})

    for field in (
        "order_confirmation",
        "payment_received",
        "shipping_update",
        "delivery_confirmation",
        "abandoned_cart",
        "require_order_confirmation",
    ):
        val = getattr(request, field, None)
        if val is not None:
            toggles[field] = val

    store.settings = store_settings
    await store_repo.update(store)

    return await get_notification_settings(store)


# ── Analytics ──


@router.get(
    "/analytics",
    response_model=SuccessResponse[WhatsAppAnalytics],
    summary="Get WhatsApp analytics",
    operation_id="get_whatsapp_analytics",
)
async def get_analytics(
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
):
    """Aggregated message stats for the store."""
    days = {"7d": 7, "30d": 30, "90d": 90}[period]
    since = datetime.now(UTC) - timedelta(days=days)

    # Total counts by status
    stats_query = (
        select(
            MessageLogModel.status,
            func.count(MessageLogModel.id),
        )
        .where(
            MessageLogModel.store_id == store.id,
            MessageLogModel.created_at >= since,
        )
        .group_by(MessageLogModel.status)
    )
    result = await db.execute(stats_query)
    status_counts = {row[0]: row[1] for row in result.all()}

    total_sent = (
        status_counts.get("sent", 0)
        + status_counts.get("delivered", 0)
        + status_counts.get("read", 0)
    )
    total_delivered = status_counts.get("delivered", 0) + status_counts.get("read", 0)
    total_read = status_counts.get("read", 0)
    total_failed = status_counts.get("failed", 0)

    delivery_rate = (total_delivered / total_sent * 100) if total_sent > 0 else 0
    read_rate = (total_read / total_delivered * 100) if total_delivered > 0 else 0

    # Daily stats
    daily_query = (
        select(
            func.date(MessageLogModel.created_at).label("day"),
            MessageLogModel.status,
            func.count(MessageLogModel.id),
        )
        .where(
            MessageLogModel.store_id == store.id,
            MessageLogModel.created_at >= since,
        )
        .group_by("day", MessageLogModel.status)
        .order_by("day")
    )
    daily_result = await db.execute(daily_query)
    day_map: dict[str, dict[str, int]] = {}
    for row in daily_result.all():
        d = str(row[0])
        day_map.setdefault(d, {"sent": 0, "delivered": 0, "read": 0, "failed": 0})
        s = row[1]
        if s in day_map[d]:
            day_map[d][s] += row[2]

    daily_stats = [
        WhatsAppDayStat(date=d, **counts) for d, counts in sorted(day_map.items())
    ]

    # Active conversations
    conv_count = await db.execute(
        select(func.count(WhatsAppConversationModel.id)).where(
            WhatsAppConversationModel.store_id == store.id,
            WhatsAppConversationModel.status == "active",
        )
    )
    active_conversations = conv_count.scalar() or 0

    return SuccessResponse(
        data=WhatsAppAnalytics(
            period=period,
            total_sent=total_sent,
            total_delivered=total_delivered,
            total_read=total_read,
            total_failed=total_failed,
            delivery_rate=round(delivery_rate, 1),
            read_rate=round(read_rate, 1),
            active_conversations=active_conversations,
            daily_stats=daily_stats,
        ),
        message="Analytics retrieved",
    )


# ─────────────────────────────────────────────────────────────────────
# US4 — Bring-Your-Own Meta WABA (FR-019 .. FR-025, backend-030)
# ─────────────────────────────────────────────────────────────────────
#
# These endpoints serve the manual-paste credential path (a merchant
# entering an access_token + phone_number_id + waba_id + app_secret
# directly), as distinct from the JS-SDK-driven /complete-signup
# endpoint above which exchanges a Meta OAuth code. The new endpoints
# use the WhatsAppStatus / BYOConnectRequest schemas from
# api/v1/schemas/stores/whatsapp_connection.py per the
# whatsapp-connection.openapi.yaml contract.


@router.post(
    "/byo/connect",
    status_code=status.HTTP_201_CREATED,
    response_model=WhatsAppStatus,
    summary="Connect merchant's own Meta WABA (BYO)",
    operation_id="connect_byo_credentials",
)
async def byo_connect(
    body: BYOConnectRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WhatsAppStatus:
    """3-step validate + encrypt + persist + reset toggles (FR-019..FR-025)."""
    from src.application.use_cases.whatsapp.connect_byo_credentials import (
        BYOValidationError,
        ConnectBYOCredentialsUseCase,
    )

    use_case = ConnectBYOCredentialsUseCase(db)
    try:
        result = await use_case.execute(
            store_id=store.id,
            access_token=body.access_token,
            phone_number_id=body.phone_number_id,
            waba_id=body.waba_id,
            app_secret=body.app_secret,
        )
    except BYOValidationError as exc:
        # 422 with whitelisted Meta error fields only (TASK-SEC-009).
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=BYOValidationFailure.model_validate(
                exc.to_response_dict()
            ).model_dump(),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("byo_connect_failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "internal_error", "message": "BYO connect failed."},
        ) from exc

    return WhatsAppStatus.model_validate(result)


@router.delete(
    "/byo/disconnect",
    summary="Disconnect BYO Meta WABA and revert to platform-managed",
    operation_id="disconnect_byo_credentials",
)
async def byo_disconnect(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Reverts mode to platform_managed; restores prior toggle snapshot."""
    from src.api.v1.schemas.stores.whatsapp_connection import WhatsAppStatus
    from src.application.use_cases.whatsapp.disconnect_byo_credentials import (
        DisconnectBYOCredentialsUseCase,
    )

    use_case = DisconnectBYOCredentialsUseCase(db)
    try:
        result = await use_case.execute(store_id=store.id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "store_not_found", "message": str(exc)},
        ) from exc

    return WhatsAppStatus.model_validate(result)


@router.get(
    "/byo/status",
    summary="Get WhatsApp connection status (BYO-aware)",
    operation_id="get_whatsapp_status_v2",
)
async def byo_status(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Returns the WhatsAppStatus shape per the BYO contract. Distinct
    from the legacy /status endpoint which uses the older
    WhatsAppConnectionStatus schema kept for backward compatibility.
    """
    from src.api.v1.schemas.stores.whatsapp_connection import (
        NotificationSettings as NotifSettings,
    )
    from src.api.v1.schemas.stores.whatsapp_connection import WhatsAppStatus

    cred = (
        await db.execute(
            select(ServiceCredential).where(
                ServiceCredential.tenant_id == store.tenant_id,
                ServiceCredential.service_type == ServiceType.WHATSAPP,
                ServiceCredential.service_name == ServiceName.WHATSAPP_BUSINESS,
                ServiceCredential.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    store_settings = store.settings or {}
    notifs = store_settings.get("whatsapp_notifications") or {}
    wa_settings = store_settings.get("whatsapp") or {}
    credential_error = wa_settings.get("credential_error")

    if cred and cred.extra_metadata:
        return WhatsAppStatus(
            mode="byo",
            connected=True,
            phone_display_name=cred.extra_metadata.get("display_name"),
            display_phone_number=cred.extra_metadata.get("phone_number"),
            waba_id=cred.extra_metadata.get("waba_id"),
            last_validated_at=cred.last_validated_at,
            credential_error=credential_error,
            notifications=NotifSettings(**notifs) if notifs else NotifSettings(),
        )

    # Platform-managed
    return WhatsAppStatus(
        mode="platform_managed",
        connected=bool(settings.whatsapp_enabled and settings.whatsapp_phone_number_id),
        phone_display_name="NUMU" if settings.whatsapp_enabled else None,
        display_phone_number=None,
        waba_id=None,
        last_validated_at=None,
        credential_error=None,
        notifications=NotifSettings(**notifs) if notifs else NotifSettings(),
    )


@router.patch(
    "/byo/notifications",
    summary="Update notification toggles (BYO-path)",
    operation_id="update_whatsapp_notifications_v2",
)
async def byo_update_notifications(
    body: dict,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Partial update of ``store.settings.whatsapp_notifications.*``.

    Distinct from the legacy PATCH /notifications endpoint which writes
    to a different settings path (``store.settings.whatsapp.notification_toggles``)
    for backward compatibility with the embedded-signup UI. This endpoint
    writes to the canonical path that the order-event handlers + the
    send guard read from (FR-019a).
    """
    from src.api.v1.schemas.stores.whatsapp_connection import (
        NotificationSettings as NotifSettings,
    )

    allowed_keys = set(NotifSettings.model_fields.keys())
    updates = {k: bool(v) for k, v in body.items() if k in allowed_keys}

    store_settings = dict(store.settings or {})
    current = dict(store_settings.get("whatsapp_notifications") or {})
    current.update(updates)
    store_settings["whatsapp_notifications"] = current
    store.settings = store_settings

    store_repo = StoreRepository(db)
    await store_repo.update(store)

    return NotifSettings(**current)

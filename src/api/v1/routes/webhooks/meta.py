"""Meta (Facebook/Instagram) messaging webhook receiver.

WhatsApp Cloud API events use ``/webhooks/whatsapp/callback``; this route
only receives Facebook Page (``object: "page"``) and Instagram
(``object: "instagram"``) messaging events for the omnichannel inbox.
"""

import json
import time
import uuid
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.entities.channel_connection import ChannelType
from src.core.logging import get_logger
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.external_services.meta.signature import (
    parse_signed_request,
    verify_meta_webhook,
    verify_x_hub_signature,
)

logger = get_logger(__name__)

router = APIRouter(tags=["Webhooks - Meta"])

# Top-level ``object`` discriminator → inbox channel.
_OBJECT_CHANNELS = {
    "page": ChannelType.FACEBOOK,
    "instagram": ChannelType.INSTAGRAM,
}


@router.get("")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> PlainTextResponse:
    """Webhook verification handshake — Meta sends GET with hub.* params."""
    challenge = verify_meta_webhook(
        mode=hub_mode or "",
        token=hub_verify_token or "",
        challenge=hub_challenge or "",
    )
    if challenge is not None:
        return PlainTextResponse(content=challenge, status_code=status.HTTP_200_OK)
    return PlainTextResponse(
        content="Verification failed", status_code=status.HTTP_403_FORBIDDEN
    )


@router.post("")
async def receive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    x_hub_signature_256: str | None = Header(None, alias="x-hub-signature-256"),
) -> PlainTextResponse:
    """Receive Facebook Page / Instagram messaging webhook events.

    Signature is verified over the RAW request bytes (re-serialized JSON
    never matches Meta's HMAC). Uses an admin DB session because webhooks
    arrive without tenant context; tenant isolation comes from resolving
    the ChannelConnection row for the receiving page/IG account.
    """
    from src.infrastructure.webhooks.meta_handlers import (
        handle_message_status_webhook,
        handle_message_webhook,
    )

    start_time = time.perf_counter()
    event_id = str(uuid.uuid4())

    raw_body = await request.body()

    if settings.meta_app_secret:
        if not x_hub_signature_256 or not verify_x_hub_signature(
            raw_body, x_hub_signature_256
        ):
            logger.warning(
                "meta_webhook_signature_rejected",
                event_id=event_id,
                signature_present=bool(x_hub_signature_256),
            )
            return PlainTextResponse(
                content="Invalid signature", status_code=status.HTTP_403_FORBIDDEN
            )
    else:
        # Dev only — prod must configure META_APP_SECRET.
        logger.warning("meta_webhook_unverified_no_app_secret", event_id=event_id)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return PlainTextResponse(
            content="Invalid payload", status_code=status.HTTP_400_BAD_REQUEST
        )

    channel = _OBJECT_CHANNELS.get(payload.get("object"))
    if channel is None:
        # Unknown object type (e.g. permissions fields we haven't onboarded).
        # Acknowledge with 200 so Meta doesn't retry-storm us.
        logger.warning(
            "meta_webhook_unhandled_object",
            event_id=event_id,
            object_type=payload.get("object"),
        )
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK)

    entries = payload.get("entry", [])
    for e in entries:
        entry_id = e.get("id")
        for msg in e.get("messaging", []):
            # Per-event isolation: one malformed event must not drop the
            # rest of the batch (Meta batches events per request).
            try:
                if msg.get("message"):
                    await handle_message_webhook(db, msg, channel, entry_id=entry_id)
                elif msg.get("delivery") or msg.get("read"):
                    await handle_message_status_webhook(msg)
            except Exception:
                logger.exception("meta_webhook_event_failed", event_id=event_id)

    # Surface commit failure as 5xx so Meta redelivers instead of the
    # events being silently lost.
    try:
        await db.commit()
    except Exception:
        logger.exception("meta_webhook_commit_failed", event_id=event_id)
        try:
            await db.rollback()
        except Exception:
            logger.exception("meta_webhook_rollback_failed", event_id=event_id)
        return PlainTextResponse(
            content="Persistence failed",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    latency_ms = (time.perf_counter() - start_time) * 1000
    logger.info(
        "meta_webhook_received",
        event_id=event_id,
        channel=channel.value,
        latency_ms=round(latency_ms, 2),
        entry_count=len(entries),
    )

    return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK)


@router.post("/deauthorize")
async def deauthorize_callback(
    signed_request: str = Form(...),
    db: AsyncSession = Depends(get_admin_db_session),
) -> dict:
    """Meta deauthorize callback — a user removed the app.

    Configured in the App Dashboard (App settings → Basic). We verify the
    signed_request and record the event; connection revocation for the
    affected pages surfaces on the next token-health check because Meta
    does not tell us which business assets the removing user managed.
    """
    from src.infrastructure.database.models.public.omnichannel import (
        WebhookEventModel,
    )

    data = parse_signed_request(signed_request)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signed_request"
        )

    user_id = str(data.get("user_id", ""))
    db.add(
        WebhookEventModel(
            provider="meta",
            event_type="deauthorize",
            external_id=user_id or None,
            payload={"user_id": user_id},
            received_at=datetime.now(UTC),
            processed_at=datetime.now(UTC),
            status="processed",
        )
    )
    await db.commit()

    logger.info("meta_deauthorize_received", user_id=user_id)
    return {"status": "ok"}


@router.post("/data-deletion")
async def data_deletion_callback(
    signed_request: str = Form(...),
    db: AsyncSession = Depends(get_admin_db_session),
) -> dict:
    """Meta data deletion request callback.

    Required for App Review. Verifies the signed_request, deletes any
    conversation threads whose participant matches the requesting user id
    (messages cascade at the DB level), records a confirmation, and
    returns the ``{url, confirmation_code}`` shape Meta expects.
    """
    from src.infrastructure.database.models.public.omnichannel import (
        MessageThreadModel,
        WebhookEventModel,
    )

    data = parse_signed_request(signed_request)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signed_request"
        )

    user_id = str(data.get("user_id", ""))
    confirmation_code = uuid.uuid4().hex

    deleted_threads = 0
    if user_id:
        result = await db.execute(
            delete(MessageThreadModel).where(
                MessageThreadModel.external_participant_id == user_id
            )
        )
        deleted_threads = result.rowcount or 0

    db.add(
        WebhookEventModel(
            provider="meta",
            event_type="data_deletion",
            external_id=confirmation_code,
            payload={"user_id": user_id, "deleted_threads": deleted_threads},
            received_at=datetime.now(UTC),
            processed_at=datetime.now(UTC),
            status="completed",
        )
    )
    await db.commit()

    logger.info(
        "meta_data_deletion_processed",
        user_id=user_id,
        confirmation_code=confirmation_code,
        deleted_threads=deleted_threads,
    )

    status_url = (
        "https://numueg.app/api/v1/webhooks/meta/data-deletion/status"
        f"?code={confirmation_code}"
    )
    return {"url": status_url, "confirmation_code": confirmation_code}


@router.get("/data-deletion/status")
async def data_deletion_status(
    code: str = Query(..., min_length=8, max_length=64),
    db: AsyncSession = Depends(get_admin_db_session),
) -> dict:
    """Human-checkable status page for a data deletion request."""
    from src.infrastructure.database.models.public.omnichannel import (
        WebhookEventModel,
    )

    result = await db.execute(
        select(WebhookEventModel).where(
            WebhookEventModel.provider == "meta",
            WebhookEventModel.event_type == "data_deletion",
            WebhookEventModel.external_id == code,
        )
    )
    event = result.scalar_one_or_none()
    if not event:
        return {"code": code, "status": "unknown"}
    return {
        "code": code,
        "status": event.status,
        "processed_at": event.processed_at.isoformat() if event.processed_at else None,
    }


__all__ = ["router"]

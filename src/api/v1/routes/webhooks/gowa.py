"""GOWA webhook handler.

Inbound events from the self-hosted go-whatsapp-web-multidevice instance. GOWA
pushes rather than exposes state, so this route is the ONLY way delivery status
and customer replies reach the platform:

* ``message.ack`` — delivery receipts. There is no status endpoint to poll, so
  the acks recorded here are the entire basis for "was it delivered".
* ``message`` — inbound text. Chiefly the numbered replies that stand in for
  Meta's quick-reply buttons on this transport.

## Why replies are handled the way they are

Meta delivers a button tap with its meaning attached (``button.payload`` =
``<action>:<subdomain>/<order_id>``). whatsmeow has no buttons, so the same
customer intent arrives as the bare text ``"1"``. The payload was recorded
against the recipient when the prompt went out (see
``whatsapp_gowa_pending_replies``), so this route looks it up and calls the
SAME handlers the Meta webhook calls, with byte-identical input. The two
transports therefore cannot diverge in how a COD order gets confirmed.

## Always answer 200

An error here must not make GOWA retry a message that already had its effect.
Every handler is idempotent and defensive, and failures are logged and
swallowed — same contract the Meta webhook honours.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.external_services.whatsapp.gowa_provider import GowaProvider

logger = logging.getLogger(__name__)
router = APIRouter()

# Signature verification only needs the secret, not a device, so a bare
# instance is fine here.
_verifier = GowaProvider(device_id="")

_OK = JSONResponse({"status": "ok"}, status_code=status.HTTP_200_OK)

# A reply counts as a choice only when it is essentially JUST the answer — a
# digit ("1", "1.") or the button's own wording ("تأكيد الأوردر", "confirm").
# People answer with words at least as often as numbers, and a reply we cannot
# read leaves a COD order stuck; but "1 more thing please" must never be taken
# as a confirmation, so anything sentence-length is ignored.
_MAX_CHOICE_WORDS = 4
_MAX_CHOICE_CHARS = 40


def _extract_choice(text: str) -> str | None:
    """Normalised choice key for a reply, or None when it isn't an answer.

    Returns something to look up in the pending prompt's payload map, which is
    keyed by BOTH digits and normalised labels (see whatsapp_plain_render).
    """
    from src.core.whatsapp_plain_render import normalise_reply

    raw = (text or "").strip().rstrip(".)-")
    if not raw or len(raw) > _MAX_CHOICE_CHARS:
        return None
    if raw.isdigit():
        return raw
    key = normalise_reply(raw)
    if not key or len(key.split(" ")) > _MAX_CHOICE_WORDS:
        return None
    return key


@router.post("/callback", operation_id="gowa_webhook")
async def gowa_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_admin_db_session),
) -> JSONResponse:
    """Receive a GOWA event.

    Rejects anything whose HMAC does not verify — this endpoint can drive order
    state, so an unauthenticated caller must not be able to confirm or cancel
    someone's order.
    """
    raw = await request.body()
    payload = _verifier.verify_webhook_signature(raw, x_hub_signature_256 or "")
    if payload is None:
        logger.warning("gowa_webhook_rejected_bad_signature")
        return JSONResponse(
            {"status": "invalid signature"}, status_code=status.HTTP_401_UNAUTHORIZED
        )

    # GOWA's envelope is {event, device_id, payload:{...}}. `device_id` is the
    # device's own JID ("201002599455@s.whatsapp.net"), NOT the UUID we store —
    # reading either of those wrongly makes every inbound message fall through
    # in silence, which is exactly what happened.
    event = str(payload.get("event") or payload.get("type") or "")
    device_jid = str(payload.get("device_id") or "")
    body = payload.get("payload")
    if not isinstance(body, dict):
        body = {}

    try:
        if device_jid:
            await _touch_device(db, device_jid, event, body)
        if event.startswith("message.ack"):
            await _record_ack(db, body)
        elif event == "message":
            await _process_inbound(db, device_jid, body)
        await db.commit()
    except Exception:
        # Never let a handler failure turn into a retry storm.
        logger.exception("gowa_webhook_processing_failed", extra={"event": event})

    return _OK


async def _resolve_device(db: AsyncSession, device_jid: str):
    """Find our device row from GOWA's device JID.

    The webhook identifies the device by its WhatsApp JID, while we key rows by
    GOWA's UUID. The stored `phone` is the bridge.
    """
    from src.infrastructure.repositories.whatsapp_gowa_device_repository import (
        WhatsAppGowaDeviceRepository,
    )

    digits = device_jid.split("@", 1)[0]
    if not digits:
        return None
    repo = WhatsAppGowaDeviceRepository(db)
    # Try the UUID form too — harmless, and keeps this working if GOWA ever
    # sends the id instead.
    device = await repo.get_by_device_id(device_jid)
    if device:
        return device
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.whatsapp_gowa_device import (
        WhatsAppGowaDeviceModel,
    )

    result = await db.execute(
        select(WhatsAppGowaDeviceModel)
        .where(
            WhatsAppGowaDeviceModel.phone == f"+{digits}",
            WhatsAppGowaDeviceModel.is_active.is_(True),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _touch_device(
    db: AsyncSession, device_id: str, event: str, payload: dict
) -> None:
    """Keep the device row's liveness and status current.

    Staleness in ``last_seen_at`` is how a silently dead session is spotted,
    and ``logged_out`` is how a ban presents on this transport — so both are
    recorded on every event rather than only on explicit status changes.
    """
    from src.infrastructure.repositories.whatsapp_gowa_device_repository import (
        WhatsAppGowaDeviceRepository,
    )

    repo = WhatsAppGowaDeviceRepository(db)
    lowered = event.lower()
    if "logout" in lowered or "logged_out" in lowered:
        await repo.mark_status(
            device_id, "logged_out", str(payload.get("reason") or event)
        )
    elif "disconnect" in lowered:
        await repo.mark_status(
            device_id, "disconnected", str(payload.get("reason") or "")
        )
    elif "connected" in lowered or "login" in lowered or "paired" in lowered:
        phone = payload.get("phone") or payload.get("jid")
        await repo.mark_connected(device_id, str(phone) if phone else None)
    else:
        await repo.touch(device_id)


async def _record_ack(db: AsyncSession, payload: dict) -> None:
    """Persist a delivery receipt against the outbound message log.

    GOWA's ack levels map onto our statuses; anything unrecognised is left
    alone rather than guessed at. Out-of-order acks are safe regardless —
    ``MessageLogRepository.update_status`` only accepts forward progress
    (QUEUED→SENT→DELIVERED→READ), so a late "sent" cannot undo a "read".
    """
    from src.core.entities.message_log import MessageStatus as LogStatus
    from src.infrastructure.repositories.message_log_repository import (
        MessageLogRepository,
    )

    # `ids` is an ARRAY of affected message ids, and the level is
    # `receipt_type` ("delivered" / "read").
    ids = payload.get("ids")
    if isinstance(ids, list):
        message_ids = [str(i) for i in ids if i]
    else:
        single = payload.get("message_id") or payload.get("id")
        message_ids = [str(single)] if single else []
    if not message_ids:
        return

    raw_status = str(
        payload.get("receipt_type") or payload.get("ack") or payload.get("status") or ""
    ).lower()
    mapping = {
        "delivered": LogStatus.DELIVERED,
        "device": LogStatus.DELIVERED,
        "read": LogStatus.READ,
        "played": LogStatus.READ,
        "sent": LogStatus.SENT,
        "server": LogStatus.SENT,
        "error": LogStatus.FAILED,
    }
    mapped = mapping.get(raw_status)
    if mapped is None:
        return

    repo = MessageLogRepository(db)
    for message_id in message_ids:
        try:
            await repo.update_status(message_id, mapped)
        except Exception:
            logger.exception("gowa_ack_update_failed", extra={"message_id": message_id})


async def _record_inbound(
    db: AsyncSession,
    *,
    store_id,
    tenant_id,
    phone: str,
    text: str,
    message_id: str,
) -> None:
    """Persist an inbound message to the log and the conversation inbox.

    Both are what the merchant hub renders: the WhatsApp dashboard counts from
    MessageLog, and the inbox lists WhatsAppConversation. Best-effort — a
    logging failure must not stop the reply being acted on, because acting on
    it is the part the customer is waiting for.
    """
    from src.core.entities.message_log import MessageDirection, MessageLog
    from src.core.entities.message_log import MessageStatus as LogStatus
    from src.infrastructure.repositories.message_log_repository import (
        MessageLogRepository,
    )
    from src.infrastructure.repositories.whatsapp_conversation_repository import (
        WhatsAppConversationRepository,
    )

    preview = (text or "").strip()[:255] or "[message]"
    try:
        await MessageLogRepository(db).create(
            MessageLog(
                tenant_id=tenant_id,
                store_id=store_id,
                phone=phone,
                message_id=message_id or f"gowa-in-{phone}",
                direction=MessageDirection.INBOUND,
                template_name=None,
                content=preview,
                status=LogStatus.DELIVERED,
            )
        )
    except Exception:
        logger.exception("gowa_inbound_log_failed")

    try:
        await WhatsAppConversationRepository(db).upsert_on_message(
            store_id=store_id,
            tenant_id=tenant_id,
            phone=phone,
            name=None,
            message_preview=preview,
            direction="inbound",
        )
    except Exception:
        logger.exception("gowa_inbound_conversation_failed")


async def _process_inbound(db: AsyncSession, device_jid: str, body: dict) -> None:
    """Handle an inbound message — principally a numbered reply.

    Resolves the digit to the payload recorded when the prompt was sent, then
    dispatches through the SAME action handlers the Meta webhook uses, so the
    two transports cannot diverge on how a COD order gets confirmed.
    """
    from functools import partial

    from src.application.services.cod_autopilot_service import (
        handle_delivery_response,
        handle_shipall,
    )
    from src.application.services.order_confirmation_service import (
        cancel_order_from_whatsapp,
        confirm_order_from_whatsapp,
        parse_quick_reply_action,
        postpone_order_from_whatsapp,
    )
    from src.infrastructure.repositories.message_log_repository import (
        MessageLogRepository,
    )
    from src.infrastructure.repositories.whatsapp_gowa_pending_reply_repository import (
        WhatsAppGowaPendingReplyRepository,
    )

    text = str(body.get("body") or body.get("text") or "")
    from_jid = str(body.get("from") or body.get("chat_id") or "")
    # JIDs look like "201060082542@s.whatsapp.net"; the platform stores phones
    # as canonical E.164.
    digits = from_jid.split("@", 1)[0].split(":", 1)[0]
    if not digits:
        return
    from_phone = f"+{digits}"

    # Which store does this belong to?
    #
    # The device is the obvious answer for a merchant's OWN number, but the
    # shared platform device has no store_id — it sends for the whole fleet —
    # so it cannot answer this. Fall back the way the Meta webhook does: the
    # most recent message we sent this person tells us whose customer they are.
    store_id = tenant_id = None
    device = await _resolve_device(db, device_jid)
    if device and device.store_id:
        store_id, tenant_id = device.store_id, device.tenant_id
    else:
        prior = await MessageLogRepository(db).get_latest_by_phone(from_phone)
        if prior:
            store_id, tenant_id = prior.store_id, prior.tenant_id

    if store_id and tenant_id:
        await _record_inbound(
            db,
            store_id=store_id,
            tenant_id=tenant_id,
            phone=from_phone,
            text=text,
            message_id=str(body.get("id") or ""),
        )

    # ── STOP / opt-out ─────────────────────────────────────────────────────
    #
    # Runs BEFORE the digit parser and wins outright. An unhonoured opt-out is
    # the most direct route to a report, and reports are what get a number
    # banned on this transport.
    from src.core.services.whatsapp_stop_keyword_detector import is_stop_keyword

    if text and is_stop_keyword(text) and store_id:
        try:
            from src.application.use_cases.whatsapp.opt_out_customer import (
                OptOutCustomerUseCase,
            )

            await OptOutCustomerUseCase(db).execute(
                store_id=store_id, phone=from_phone, reason="inbound_stop_keyword"
            )
            logger.info(
                "gowa_stop_keyword_opt_out",
                extra={"store_id": str(store_id), "phone_tail": from_phone[-4:]},
            )
        except Exception:
            logger.exception("gowa_stop_opt_out_failed")
        return

    choice = _extract_choice(text)
    if not choice:
        return

    repo = WhatsAppGowaPendingReplyRepository(db)
    resolved = await repo.resolve(from_phone, choice)
    if not resolved:
        logger.info("gowa_reply_no_pending_prompt", extra={"choice": choice})
        return
    row, reply_payload = resolved

    handlers = {
        "confirm": confirm_order_from_whatsapp,
        "postpone": postpone_order_from_whatsapp,
        "cancel": cancel_order_from_whatsapp,
        "shipall": handle_shipall,
        "dlvyes": partial(handle_delivery_response, action="dlvyes"),
        "dlvnot": partial(handle_delivery_response, action="dlvnot"),
        "dlvref": partial(handle_delivery_response, action="dlvref"),
    }
    handler = handlers.get(
        parse_quick_reply_action(reply_payload), confirm_order_from_whatsapp
    )
    try:
        await handler(db, payload=reply_payload, from_phone=from_phone)
        # Consume only after the action succeeded, so a transient failure
        # leaves the prompt answerable rather than burning the reply.
        await repo.mark_consumed(row.id)
        logger.info(
            "gowa_reply_applied",
            extra={"choice": choice, "action": parse_quick_reply_action(reply_payload)},
        )
    except Exception:
        logger.exception("gowa_reply_handler_failed")

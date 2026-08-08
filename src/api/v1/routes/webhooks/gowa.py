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

# A reply is treated as a numbered choice only if it is essentially just the
# digit. "1" and "1." count; "1 more thing" does not — acting on that would
# confirm an order the customer never meant to confirm.
_MAX_DIGIT_REPLY_LEN = 3


def _extract_digit(text: str) -> str | None:
    """The digit a customer meant, or None when the message isn't a choice."""
    stripped = (text or "").strip().rstrip(".)-")
    if not stripped or len(stripped) > _MAX_DIGIT_REPLY_LEN:
        return None
    return stripped if stripped.isdigit() else None


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

    event = str(payload.get("event") or payload.get("type") or "")
    device_id = str(payload.get("device_id") or payload.get("deviceId") or "")

    try:
        if device_id:
            await _touch_device(db, device_id, event, payload)
        if event.startswith("message.ack"):
            await _record_ack(db, payload)
        elif event == "message" or "message" in payload:
            await _process_inbound(db, payload)
        await db.commit()
    except Exception:
        # Never let a handler failure turn into a retry storm.
        logger.exception("gowa_webhook_processing_failed", extra={"event": event})

    return _OK


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

    message_id = str(
        payload.get("message_id") or payload.get("id") or payload.get("ids") or ""
    )
    if not message_id:
        return

    raw_status = str(payload.get("ack") or payload.get("status") or "").lower()
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

    try:
        await MessageLogRepository(db).update_status(message_id, mapped)
    except Exception:
        logger.exception("gowa_ack_update_failed", extra={"message_id": message_id})


async def _process_inbound(db: AsyncSession, payload: dict) -> None:
    """Handle an inbound message — principally a numbered reply.

    Resolves the digit to the payload recorded when the prompt was sent, then
    dispatches through the SAME action handlers the Meta webhook uses.
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
    from src.infrastructure.repositories.whatsapp_gowa_pending_reply_repository import (
        WhatsAppGowaPendingReplyRepository,
    )

    message = (
        payload.get("message") if isinstance(payload.get("message"), dict) else payload
    )
    text = str(
        message.get("text") or message.get("body") or message.get("conversation") or ""
    )
    from_phone = str(
        payload.get("from") or message.get("from") or payload.get("sender") or ""
    )
    # GOWA reports the sender as a JID ("201001234567@s.whatsapp.net"); the
    # pending rows are keyed by canonical E.164, matching how the rest of the
    # platform stores phones.
    if "@" in from_phone:
        from_phone = from_phone.split("@", 1)[0]
    if from_phone and not from_phone.startswith("+"):
        from_phone = f"+{from_phone}"

    digit = _extract_digit(text)
    if not digit or not from_phone:
        return

    repo = WhatsAppGowaPendingReplyRepository(db)
    resolved = await repo.resolve(from_phone, digit)
    if not resolved:
        logger.info(
            "gowa_reply_no_pending_prompt",
            extra={"digit": digit},
        )
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
        # leaves the prompt answerable rather than burning the customer's reply.
        await repo.mark_consumed(row.id)
    except Exception:
        logger.exception("gowa_reply_handler_failed")

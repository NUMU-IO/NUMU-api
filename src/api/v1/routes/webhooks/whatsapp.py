"""WhatsApp webhook handler.

Handles incoming webhooks from WhatsApp Business API:
- Message status updates (sent, delivered, read)
- Incoming messages from customers
- Webhook verification challenge

Agent collaboration:
- WhatsApp Agent: signature verification + event dispatch
- Repository Agent: persists MessageLog entries for every event
- Security Agent: tenant context applied via admin session
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.config import settings
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.external_services.whatsapp import WhatsAppMessagingService
from src.infrastructure.repositories.message_log_repository import (
    MessageLogRepository,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize service
whatsapp_service = WhatsAppMessagingService()


@router.get("/callback", operation_id="whatsapp_verify")
async def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Handle WhatsApp webhook verification challenge.

    When you configure the webhook URL in Meta Developer Console,
    WhatsApp sends a GET request with a challenge to verify.

    Query params:
    - hub.mode: Should be "subscribe"
    - hub.verify_token: Token you configured in Meta console
    - hub.challenge: Challenge string to return

    Returns:
        The challenge string if verification succeeds
    """
    if hub_mode != "subscribe":
        logger.warning(f"WhatsApp webhook verify: unexpected mode {hub_mode}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid mode",
        )

    # Verify the token matches what we configured
    expected_token = settings.whatsapp_webhook_verify_token
    if not expected_token:
        logger.warning("WhatsApp verify token not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook not configured",
        )

    if hub_verify_token != expected_token:
        logger.warning("WhatsApp webhook verify: token mismatch")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification failed",
        )

    logger.info("WhatsApp webhook verified successfully")
    return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge


@router.post("/callback", operation_id="whatsapp_callback")
async def whatsapp_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    x_hub_signature_256: str = Header(None, alias="x-hub-signature-256"),
):
    """Handle WhatsApp webhook notifications.

    WhatsApp sends POST requests for:
    - Message status updates (sent, delivered, read, failed)
    - Incoming messages from customers
    - Template message status

    The x-hub-signature-256 header contains HMAC signature.

    Uses an admin DB session (RLS bypass) because webhooks arrive
    without tenant context. Tenant isolation is enforced at the
    application level via explicit tenant_id on every log entry.
    """
    payload = await request.body()

    # Verify signature
    if settings.whatsapp_app_secret:
        verified_data = whatsapp_service.verify_webhook_signature(
            payload,
            x_hub_signature_256 or "",
        )
        if not verified_data:
            logger.warning("WhatsApp webhook signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
        data = verified_data
    else:
        # In development, accept without verification
        import json

        data = json.loads(payload)
        logger.warning(
            "WhatsApp webhook received without signature verification (dev mode)"
        )

    # Process the webhook event
    try:
        # Extract the webhook data
        object_type = data.get("object")

        if object_type != "whatsapp_business_account":
            logger.warning(f"Unexpected webhook object type: {object_type}")
            return {"status": "ignored", "reason": "unknown object type"}

        # Process entries
        # Build message log repository for persistence
        message_log_repo = MessageLogRepository(db)

        entries = data.get("entry", [])

        for entry in entries:
            _account_id = entry.get("id")
            changes = entry.get("changes", [])

            for change in changes:
                field = change.get("field")
                value = change.get("value", {})

                if field == "messages":
                    # Delegate to the service which now persists via repo
                    await whatsapp_service.handle_webhook_event(
                        data={"entry": [{"changes": [change]}]},
                        message_log_repo=message_log_repo,
                    )

                    # Upsert conversations for inbound messages
                    await _upsert_conversations_from_webhook(
                        db, change.get("value", {})
                    )

                    # backend-031 — flip orders.customer_confirmation_status
                    # to 'confirmed' when an inbound QUICK_REPLY button
                    # arrives that's in reply to an order_confirmation_request
                    # template send. Idempotent — re-applying 'confirmed'
                    # is a no-op (we never downgrade off a terminal state).
                    await _handle_order_confirmation_reply(db, change.get("value", {}))
                elif field == "message_template_status_update":
                    # backend-030 / US5 / FR-028 — template approval
                    # status updates from Meta. Routed here by the
                    # top-level field discriminator; idempotent per
                    # TASK-SEC-008 (duplicate payloads = no-op).
                    from src.infrastructure.external_services.meta.whatsapp_template_status_webhook import (
                        handle_template_status_update,
                    )

                    await handle_template_status_update(
                        db, waba_id=str(_account_id), value=value
                    )
                else:
                    # Unknown field — log + 200 so we don't trigger Meta
                    # retry storms on subscription fields we haven't
                    # onboarded yet (CommunityRule, message_template_quality_update, etc.).
                    logger.warning(
                        "whatsapp_webhook_unhandled_field",
                        extra={"field": field, "waba_id": str(_account_id)},
                    )

        # Commit any DB mutations made by the field handlers (template
        # status updates, conversation upserts). The message-log persist
        # path commits its own session inside the service; this commit is
        # specifically for the template-status webhook handler's flushes.
        #
        # If the commit fails we MUST surface 5xx so Meta retries the
        # delivery — the previous behaviour (bare except + 200) silently
        # lost template approval status updates (sentry HIGH-1). We roll
        # back the session before re-raising so subsequent requests on a
        # pooled connection don't inherit a poisoned transaction.
        try:
            await db.commit()
        except Exception as commit_err:
            logger.error(
                "whatsapp_webhook_commit_failed",
                exc_info=commit_err,
            )
            try:
                await db.rollback()
            except Exception:
                logger.exception("whatsapp_webhook_rollback_failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook persistence failed",
            ) from commit_err

        return {"status": "received"}

    except HTTPException:
        # Re-raise FastAPI HTTPExceptions verbatim — the 503 above must
        # reach Meta as a 5xx so its retry queue picks it up.
        raise
    except Exception as e:
        logger.error(f"WhatsApp webhook processing error: {e}", exc_info=True)
        # Return 200 to acknowledge receipt even on processing error
        # to prevent WhatsApp from retrying.
        # Never leak internal error details to the external caller.
        return {"status": "error", "message": "Internal processing error"}


@router.get("/status/{message_id}", operation_id="get_message_status")
async def get_message_status(
    message_id: str,
    _user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: AsyncSession = Depends(get_admin_db_session),
):
    """Get status of a sent WhatsApp message.

    Queries the MessageLog table for the latest status.

    Args:
        message_id: WhatsApp message ID

    Returns:
        Message status information
    """
    repo = MessageLogRepository(db)
    log = await repo.get_by_message_id(message_id)
    if log:
        return {
            "message_id": message_id,
            "status": log.status,
            "direction": log.direction,
            "phone": log.phone,
            "created_at": log.created_at.isoformat(),
            "updated_at": log.updated_at.isoformat(),
        }
    return {
        "message_id": message_id,
        "status": "unknown",
        "note": "No log entry found for this message ID",
    }


async def _upsert_conversations_from_webhook(db: AsyncSession, value: dict) -> None:
    """Create/update WhatsApp conversations from inbound webhook messages.

    Also feeds short single-token replies through the verification-reply
    parser (backend-015) so a customer's "yes"/"no" actually closes the
    risk loop instead of getting buried in the conversation log.
    """
    from src.application.use_cases.shopify.handle_verification_reply import (
        apply_reply,
    )
    from src.infrastructure.repositories.whatsapp_conversation_repository import (
        WhatsAppConversationRepository,
    )

    messages = value.get("messages", [])
    contacts = value.get("contacts", [])
    contact_map = {
        c.get("wa_id", ""): c.get("profile", {}).get("name") for c in contacts
    }

    if not messages:
        return

    conv_repo = WhatsAppConversationRepository(db)

    for message in messages:
        from_number = message.get("from")
        if not from_number:
            continue

        msg_type = message.get("type")
        text_content = None
        if msg_type == "text":
            text_content = message.get("text", {}).get("body", "")
        elif msg_type == "button":
            text_content = message.get("button", {}).get("text", "")
        elif msg_type == "image":
            text_content = "[Image]"
        elif msg_type == "document":
            text_content = "[Document]"
        elif msg_type == "video":
            text_content = "[Video]"
        elif msg_type == "audio":
            text_content = "[Audio]"
        else:
            text_content = f"[{msg_type}]"

        customer_name = contact_map.get(from_number)

        # Resolve store from prior message logs
        prior = await MessageLogRepository(db).get_latest_by_phone(from_number)
        if not prior:
            logger.debug(
                "No prior messages for %s — cannot resolve store for conversation",
                from_number,
            )
            continue

        # ── STOP-keyword detection (backend-030 / FR-009, FR-010) ──────
        # Customers replying STOP / UNSUBSCRIBE / إلغاء / الغاء as the
        # first word of an inbound text message must be opted out within
        # 10s and an acknowledgement reply sent. Checked BEFORE the
        # verification-reply branch so a STOP keyword takes precedence
        # over any other interpretation (a customer trying to opt out
        # should never be classified as a verification reply).
        stop_detected = False
        if msg_type == "text" and text_content:
            from src.core.services.whatsapp_stop_keyword_detector import (
                is_stop_keyword,
            )

            if is_stop_keyword(text_content):
                stop_detected = True
                try:
                    from src.application.use_cases.whatsapp.opt_out_customer import (
                        OptOutCustomerUseCase,
                    )

                    # Phone arrives as country-code digits (e.g. "201001234567")
                    # from Meta; canonicalize to "+201001234567" for storage.
                    canon_phone = (
                        f"+{from_number}"
                        if not from_number.startswith("+")
                        else from_number
                    )
                    await OptOutCustomerUseCase(db).execute(
                        store_id=prior.store_id,
                        phone=canon_phone,
                        reason="inbound_stop_keyword",
                    )
                    await _send_optout_ack(
                        db,
                        store_id=prior.store_id,
                        tenant_id=prior.tenant_id,
                        phone=canon_phone,
                    )
                    logger.info(
                        "whatsapp_stop_keyword_opt_out",
                        phone_tail=canon_phone[-4:],
                        store_id=str(prior.store_id),
                    )
                except Exception as e:
                    logger.warning(
                        "stop_keyword_opt_out_failed for %s: %s", from_number, e
                    )

        # Verification reply path (backend-015). Only text messages can
        # carry a yes/no token; button replies are still handled here
        # since the WhatsApp template offers buttons too. STOP wins
        # outright — if the first word was STOP/إلغاء we don't try to
        # interpret it as a verification reply.
        reply_text = (
            text_content
            if (msg_type in ("text", "button") and not stop_detected)
            else None
        )
        if reply_text:
            try:
                reply_result = await apply_reply(
                    session=db, phone=from_number, text=reply_text
                )
                if reply_result.get("matched"):
                    logger.info(
                        "whatsapp_verification_reply",
                        extra=reply_result,
                    )
            except Exception as e:
                logger.warning(
                    "verification_reply_apply_failed for %s: %s", from_number, e
                )

        try:
            await conv_repo.upsert_on_message(
                store_id=prior.store_id,
                tenant_id=prior.tenant_id,
                phone=from_number,
                name=customer_name,
                message_preview=text_content,
                direction="inbound",
            )
        except Exception as e:
            logger.warning("Failed to upsert conversation for %s: %s", from_number, e)


async def _send_optout_ack(
    db: AsyncSession,
    *,
    store_id: UUID,
    tenant_id: UUID,
    phone: str,
) -> None:
    """Send the STOP-acknowledgement confirmation reply (FR-010).

    Resolves the per-store WhatsApp service and sends one of the
    seeded system templates (``optout_confirmation_en`` /
    ``optout_confirmation_ar``). The template name is in the
    ``OPT_IN_BYPASS_ALLOWLIST`` so the send guard does NOT block it
    even though we just flipped opt-out for this customer (TASK-SEC-010).

    Errors here are logged but do not bubble — the opt-out itself is
    already persisted; failing to send the ack must not prevent the
    customer's actual opt-out from taking effect.
    """
    try:
        from src.core.interfaces.services.messaging_service import MessageRecipient
        from src.infrastructure.external_services.whatsapp import get_whatsapp_service

        service = await get_whatsapp_service(store_id, db, tenant_id)
        # Use Arabic by default for EG market; falls back to en if the
        # template's `_ar` row isn't APPROVED. The send_text_message path
        # is used because we're inside the 24h customer-service window
        # (the customer just messaged in).
        recipient = MessageRecipient(phone=phone, name="", language="ar")
        await service.send_text_message(
            recipient,
            "تم إلغاء اشتراكك في رسائل واتساب. أرسل START للاشتراك مرة أخرى.",
        )
    except Exception as exc:
        logger.warning(
            "whatsapp_stop_ack_send_failed",
            phone_tail=phone[-4:],
            error=str(exc),
        )


async def _handle_order_confirmation_reply(db: AsyncSession, value: dict) -> None:
    """Detect a customer tapping the Confirm button on an
    ``order_confirmation_request_v1`` template send and flip the
    matching order's ``customer_confirmation_status`` to ``confirmed``.

    Meta delivers QUICK_REPLY taps as a ``messages[]`` entry with
    ``type == "button"`` and a ``context.id`` pointing at the original
    template send's wamid. We use that wamid to look up the
    ``message_logs`` row we wrote at send-time (PR #364's
    ``_persist_message_log``) — its ``metadata.order_id`` tells us
    which order to flip.

    Idempotent — re-applying ``confirmed`` is a no-op. Failures are
    swallowed (logged) so a stuck handler can never crash the webhook
    and trigger Meta retry storms.
    """
    from datetime import UTC, datetime

    from sqlalchemy import update as sa_update

    from src.infrastructure.database.models.tenant.message_log import MessageLogModel
    from src.infrastructure.database.models.tenant.order import OrderModel

    messages = (value or {}).get("messages") or []
    for msg in messages:
        # Only button replies are interesting. Text / image / etc.
        # inbound messages already get persisted by the messaging
        # service's webhook handler.
        if msg.get("type") != "button":
            continue
        button = msg.get("button") or {}
        ctx = msg.get("context") or {}
        template_wamid = ctx.get("id")
        if not template_wamid:
            continue

        try:
            log_row = (
                await db.execute(
                    select(MessageLogModel).where(
                        MessageLogModel.message_id == template_wamid
                    )
                )
            ).scalar_one_or_none()
        except Exception:
            logger.exception(
                "whatsapp_button_reply_log_lookup_failed",
                extra={"wamid": template_wamid},
            )
            continue

        if log_row is None:
            # No matching outbound — could be a button on a template
            # we didn't send (BYO store using their own WABA on the
            # platform webhook URL, etc.). Silently ignore.
            logger.info(
                "whatsapp_button_reply_no_matching_log",
                extra={"wamid": template_wamid},
            )
            continue

        # Only the order-confirmation template should flip the order
        # row. A future Cancel-button variant could route the same
        # plumbing to a different status.
        if log_row.template_name != "order_confirmation_request_v1":
            logger.debug(
                "whatsapp_button_reply_other_template",
                extra={
                    "template": log_row.template_name,
                    "wamid": template_wamid,
                },
            )
            continue

        meta = log_row.metadata_ or {}
        order_id_str = meta.get("order_id")
        if not order_id_str:
            logger.warning(
                "whatsapp_button_reply_log_no_order_id",
                extra={"wamid": template_wamid},
            )
            continue

        try:
            order_id = UUID(order_id_str)
        except (TypeError, ValueError):
            logger.warning(
                "whatsapp_button_reply_bad_order_id",
                extra={"order_id_raw": order_id_str},
            )
            continue

        # The single button on this template is Confirm. If we add a
        # second (Cancel etc.) later, branch on button.text or
        # button.payload here. For now: any button tap on this
        # template name means Confirm.
        button_text = button.get("text") or button.get("payload") or "(unknown)"
        try:
            await db.execute(
                sa_update(OrderModel)
                .where(
                    OrderModel.id == order_id,
                    # Don't downgrade a more-terminal state.
                    OrderModel.customer_confirmation_status.in_([None, "pending"]),
                )
                .values(
                    customer_confirmation_status="confirmed",
                    customer_confirmed_at=datetime.now(UTC),
                )
            )
            logger.info(
                "whatsapp_order_customer_confirmed",
                extra={
                    "order_id": str(order_id),
                    "store_id": str(log_row.store_id),
                    "button_text": button_text,
                    "wamid": template_wamid,
                },
            )
        except Exception:
            logger.exception(
                "whatsapp_order_customer_confirm_failed",
                extra={
                    "order_id": str(order_id),
                    "wamid": template_wamid,
                },
            )

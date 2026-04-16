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
                _value = change.get("value", {})

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

        return {"status": "received"}

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
    """Create/update WhatsApp conversations from inbound webhook messages."""
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

"""WhatsApp webhook handler.

Handles incoming webhooks from WhatsApp Business API:
- Message status updates (sent, delivered, read)
- Incoming messages from customers
- Webhook verification challenge
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.config import settings
from src.infrastructure.external_services.whatsapp import WhatsAppMessagingService

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
    x_hub_signature_256: str = Header(None, alias="x-hub-signature-256"),
):
    """Handle WhatsApp webhook notifications.

    WhatsApp sends POST requests for:
    - Message status updates (sent, delivered, read, failed)
    - Incoming messages from customers
    - Template message status

    The x-hub-signature-256 header contains HMAC signature.
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
        entries = data.get("entry", [])

        for entry in entries:
            account_id = entry.get("id")
            changes = entry.get("changes", [])

            for change in changes:
                field = change.get("field")
                value = change.get("value", {})

                if field == "messages":
                    # Process message-related events
                    await _process_message_event(account_id, value)

        return {"status": "received"}

    except Exception as e:
        logger.error(f"WhatsApp webhook processing error: {e}")
        # Return 200 to acknowledge receipt even on processing error
        # to prevent WhatsApp from retrying
        return {"status": "error", "message": str(e)}


async def _process_message_event(account_id: str, value: dict):
    """Process a message-related webhook event.

    Args:
        account_id: WhatsApp Business Account ID
        value: Event value containing messages or statuses
    """
    metadata = value.get("metadata", {})
    metadata.get("phone_number_id")
    metadata.get("display_phone_number")

    # Process message status updates
    statuses = value.get("statuses", [])
    for status_update in statuses:
        message_id = status_update.get("id")
        status_value = status_update.get("status")
        timestamp = status_update.get("timestamp")
        recipient_id = status_update.get("recipient_id")

        logger.info(
            f"WhatsApp message status: {message_id} -> {status_value} "
            f"for {recipient_id} at {timestamp}"
        )

        # Map WhatsApp status to our status
        status_map = {
            "sent": "sent",
            "delivered": "delivered",
            "read": "read",
            "failed": "failed",
        }
        status_map.get(status_value, "unknown")

        # TODO: Update message status in database
        # await message_repository.update_status(
        #     message_id=message_id,
        #     status=mapped_status,
        #     recipient=recipient_id,
        # )

        # Handle failed messages
        if status_value == "failed":
            errors = status_update.get("errors", [])
            for error in errors:
                error_code = error.get("code")
                error_title = error.get("title")
                error_message = error.get("message")
                logger.error(
                    f"WhatsApp message failed: {message_id}, "
                    f"code={error_code}, title={error_title}, message={error_message}"
                )

    # Process incoming messages
    messages = value.get("messages", [])
    for message in messages:
        from_number = message.get("from")
        msg_id = message.get("id")
        msg_type = message.get("type")
        timestamp = message.get("timestamp")

        logger.info(
            f"WhatsApp incoming message: {msg_id} from {from_number}, type={msg_type}"
        )

        # Handle different message types
        if msg_type == "text":
            text_body = message.get("text", {}).get("body", "")
            logger.info(f"Text message: {text_body[:100]}...")
            # TODO: Handle customer text message
            # Could trigger customer service workflow

        elif msg_type == "button":
            # Customer clicked a button in previous message
            button_payload = message.get("button", {}).get("payload")
            button_text = message.get("button", {}).get("text")
            logger.info(f"Button click: {button_text} ({button_payload})")
            # TODO: Handle button response

        elif msg_type == "interactive":
            # Customer responded to interactive message
            interactive = message.get("interactive", {})
            interactive_type = interactive.get("type")
            logger.info(f"Interactive response: {interactive_type}")
            # TODO: Handle interactive response

        # Mark message as read (optional)
        # This could be done via the API to show read receipts


@router.get("/status/{message_id}", operation_id="get_message_status")
async def get_message_status(message_id: str):
    """Get status of a sent WhatsApp message.

    Note: Status is typically delivered via webhooks.
    This endpoint is for manual status checks.

    Args:
        message_id: WhatsApp message ID

    Returns:
        Message status information
    """
    # In production, this would query the database for stored status
    # For now, return a placeholder
    return {
        "message_id": message_id,
        "status": "sent",
        "note": "Use webhooks for real-time status updates",
    }

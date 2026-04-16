"""Webhook event handlers for Meta platform events."""

import logging

logger = logging.getLogger(__name__)


async def handle_message_webhook(event: dict) -> None:
    """Handle incoming message from any channel."""
    from src.api.dependencies.repositories import (
        get_channel_connection_repository,
        get_channel_message_repository,
    )
    from src.application.use_cases.omnichannel import IngestInboundMessageUseCase

    sender_id = event.get("sender", {}).get("id")
    message_data = event.get("message", {})
    message_type = message_data.get("type", "text")
    message_id = message_data.get("mid")
    text = message_data.get("text", {}).get("body")
    attachments = message_data.get("attachments")

    if not sender_id or not message_id:
        logger.warning("Missing sender_id or message_id in webhook event")
        return

    await IngestInboundMessageUseCase(
        channel_message_repository=get_channel_message_repository,
        channel_connection_repository=get_channel_connection_repository,
    ).execute(
        connection_id=None,
        external_message_id=message_id,
        sender_id=sender_id,
        sender_name=None,
        message_type=message_type,
        body=text,
        attachment_url=attachments[0].get("payload", {}).get("url")
        if attachments
        else None,
    )


async def handle_message_status_webhook(event: dict) -> None:
    """Handle message delivery/read status webhooks."""
    statuses = event.get("statuses", [])
    for s in statuses:
        logger.debug("Status update: %s", s.get("status"))


async def handle_authentication_webhook(event: dict) -> None:
    """Handle authentication events."""
    pass


async def handle_opt_in_webhook(event: dict) -> None:
    """Handle opt-in/opt-out events."""
    pass

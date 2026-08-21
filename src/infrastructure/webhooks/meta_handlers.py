"""Webhook event handlers for Meta platform events.

Handles Facebook Messenger and Instagram DM events delivered to
``/webhooks/meta``. WhatsApp Cloud API events use their own route
(``/webhooks/whatsapp/callback``) and never reach these handlers.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.channel_connection import ChannelType

logger = logging.getLogger(__name__)


async def handle_message_webhook(
    db: AsyncSession,
    event: dict,
    channel: ChannelType,
    entry_id: str | None = None,
) -> None:
    """Ingest one inbound Messenger/Instagram message event.

    ``event`` is a single element of ``entry[].messaging[]`` (sender /
    recipient / timestamp / message). ``entry_id`` is the page or IG
    account id from the surrounding entry, used as the recipient fallback.
    """
    from src.application.use_cases.omnichannel import IngestInboundMessageUseCase
    from src.infrastructure.realtime.redis_pubsub import RealtimePublisher
    from src.infrastructure.repositories import (
        ChannelConnectionRepositoryImpl,
        ChannelMessageRepositoryImpl,
        MessageThreadRepositoryImpl,
    )

    message_data = event.get("message") or {}
    if message_data.get("is_echo"):
        # The page's own outbound message mirrored back by Meta — the send
        # path already persisted it; ingesting would create a phantom
        # inbound thread for the page itself.
        return

    sender_id = (event.get("sender") or {}).get("id")
    message_id = message_data.get("mid")
    if not sender_id or not message_id:
        logger.warning("meta_webhook_event_missing_sender_or_mid")
        return

    recipient_id = (event.get("recipient") or {}).get("id") or entry_id
    if not recipient_id:
        logger.warning("meta_webhook_event_missing_recipient")
        return

    connection_repo = ChannelConnectionRepositoryImpl(db)
    connection = await connection_repo.get_by_channel_and_external_account(
        channel=channel,
        external_account_id=str(recipient_id),
    )
    if not connection:
        logger.warning(
            "meta_webhook_no_connection_for_account channel=%s account=%s",
            channel.value,
            recipient_id,
        )
        return

    async def _fetch_sender_profile() -> tuple[str | None, str | None]:
        """Resolve the sender's display name + avatar from the Graph API.

        Uses the connection's own (page/IG) token. Cosmetic only — any
        failure is swallowed by the caller.
        """
        from src.infrastructure.external_services.meta.graph_client import (
            MetaGraphClient,
        )
        from src.infrastructure.external_services.secrets.secrets_manager import (
            SecretsManager,
        )

        if not (connection.encrypted_credentials and connection.credential_key_id):
            return None, None
        decrypted = await SecretsManager().decrypt(
            connection.encrypted_credentials, connection.credential_key_id
        )
        token = decrypted.get("access_token", "")
        if not token:
            return None, None

        client = MetaGraphClient(token)
        try:
            if channel == ChannelType.INSTAGRAM:
                data = await client.get(
                    str(sender_id), params={"fields": "name,username,profile_pic"}
                )
                name = data.get("name") or data.get("username")
                avatar = data.get("profile_pic")
            else:
                data = await client.get(
                    str(sender_id),
                    params={"fields": "first_name,last_name,profile_pic"},
                )
                name = (
                    " ".join(
                        p for p in (data.get("first_name"), data.get("last_name")) if p
                    )
                    or None
                )
                avatar = data.get("profile_pic")
            # Some Graph variants wrap the picture as {data: {url}}.
            if isinstance(avatar, dict):
                avatar = (avatar.get("data") or {}).get("url")
            return name, avatar
        finally:
            await client.close()

    # Messenger/IG events carry text as a plain string (unlike WhatsApp's
    # ``text.body``); attachments declare their own type.
    text = message_data.get("text")
    attachments = message_data.get("attachments") or []
    attachment = attachments[0] if attachments else {}
    message_type = "text" if text else attachment.get("type", "text")
    attachment_url = (attachment.get("payload") or {}).get("url")

    # Messenger timestamps are epoch milliseconds; ingest expects seconds.
    timestamp = event.get("timestamp") or 0
    if timestamp > 1_000_000_000_000:
        timestamp = timestamp // 1000
    if not timestamp:
        timestamp = int(datetime.now(UTC).timestamp())

    await IngestInboundMessageUseCase(
        channel_connection_repository=connection_repo,
        message_thread_repository=MessageThreadRepositoryImpl(db),
        channel_message_repository=ChannelMessageRepositoryImpl(db),
        realtime_publisher=RealtimePublisher(),
    ).execute(
        connection_id=connection.id,
        external_message_id=message_id,
        sender_id=str(sender_id),
        sender_name=None,
        message_type=message_type,
        body=text,
        attachment_url=attachment_url,
        external_timestamp=timestamp,
        profile_fetcher=_fetch_sender_profile,
    )


async def handle_message_status_webhook(event: dict) -> None:
    """Handle message delivery/read status webhooks."""
    delivery = event.get("delivery") or {}
    read = event.get("read") or {}
    if delivery:
        logger.debug("Meta delivery watermark: %s", delivery.get("watermark"))
    if read:
        logger.debug("Meta read watermark: %s", read.get("watermark"))
    for s in event.get("statuses", []):
        logger.debug("Status update: %s", s.get("status"))


async def handle_authentication_webhook(event: dict) -> None:
    """Handle authentication events."""
    pass


async def handle_opt_in_webhook(event: dict) -> None:
    """Handle opt-in/opt-out events."""
    pass

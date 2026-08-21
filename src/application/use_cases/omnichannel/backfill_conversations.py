"""Backfill historical conversations from Meta into the inbox.

Webhooks only deliver messages sent AFTER a connection is made, so a
merchant who connects an active Page sees an empty inbox — which reads
as broken. This walks the Graph ``/conversations`` edge once per
connection and seeds the threads and messages that already exist.

Idempotent: every message is keyed on its Meta id and skipped if
already stored, so a re-run (or a webhook arriving mid-backfill) can
never duplicate a message.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.core.entities.channel_connection import ChannelConnection, ChannelType
from src.core.entities.channel_message import (
    ChannelMessage,
    MessageDirection,
    MessageStatus,
    MessageType,
)
from src.core.entities.message_thread import MessageThread, ThreadStatus
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)
from src.core.interfaces.repositories.channel_message_repository import (
    ChannelMessageRepository,
)
from src.core.interfaces.repositories.message_thread_repository import (
    MessageThreadRepository,
)
from src.core.logging import get_logger
from src.infrastructure.external_services.meta.graph_client import (
    MetaGraphAPIError,
    MetaGraphClient,
)
from src.infrastructure.external_services.secrets.secrets_manager import SecretsManager

logger = get_logger(__name__)

# One page of conversations per Graph call; each carries its own messages.
_CONVERSATION_PAGE_SIZE = 25
_MESSAGE_FIELDS = "id,created_time,from,to,message,attachments"


class BackfillConversationsUseCase:
    """Seed threads/messages from a connection's existing Meta conversations."""

    def __init__(
        self,
        channel_connection_repository: ChannelConnectionRepository,
        message_thread_repository: MessageThreadRepository,
        channel_message_repository: ChannelMessageRepository,
        secrets_manager: SecretsManager | None = None,
    ) -> None:
        self.channel_connection_repository = channel_connection_repository
        self.message_thread_repository = message_thread_repository
        self.channel_message_repository = channel_message_repository
        self.secrets_manager = secrets_manager or SecretsManager()

    async def execute(
        self,
        connection_id: UUID,
        since_days: int = 90,
        max_conversations: int = 100,
    ) -> dict[str, Any]:
        """Backfill one connection.

        Args:
            connection_id: The channel connection to backfill.
            since_days: Ignore messages older than this many days.
            max_conversations: Hard cap on conversations walked, so a Page
                with years of history can't run unbounded.

        Returns:
            Counts of what was created, for the task log.
        """
        connection = await self.channel_connection_repository.get_by_id(connection_id)
        if not connection:
            return {"status": "skipped", "reason": "connection_missing"}
        if connection.channel == ChannelType.WHATSAPP:
            # WhatsApp Cloud API exposes no conversation history edge.
            return {"status": "skipped", "reason": "channel_unsupported"}
        if not connection.is_active():
            return {"status": "skipped", "reason": "connection_inactive"}

        token = await self._token_for(connection)
        if not token:
            return {"status": "skipped", "reason": "no_token"}

        cutoff = datetime.now(UTC) - timedelta(days=since_days)
        client = MetaGraphClient(token)
        threads_created = 0
        messages_created = 0
        conversations_seen = 0

        try:
            endpoint, params = self._conversations_query(connection)
            after: str | None = None

            while conversations_seen < max_conversations:
                page_params = dict(params)
                if after:
                    page_params["after"] = after

                try:
                    payload = await client.get(endpoint, params=page_params)
                except MetaGraphAPIError as exc:
                    # (#3)/(#200) mean the app lacks Advanced Access for this
                    # channel yet — expected before App Review, and retrying
                    # can't help. Stop quietly instead of failing the task.
                    if exc.code in (3, 10, 200):
                        logger.info(
                            "omnichannel_backfill_not_permitted",
                            connection_id=str(connection_id),
                            channel=connection.channel.value,
                            code=exc.code,
                        )
                        return {
                            "status": "skipped",
                            "reason": "insufficient_permissions",
                            "channel": connection.channel.value,
                        }
                    raise
                conversations = payload.get("data") or []
                if not conversations:
                    break

                for conversation in conversations:
                    if conversations_seen >= max_conversations:
                        break
                    conversations_seen += 1
                    created = await self._ingest_conversation(
                        connection=connection,
                        conversation=conversation,
                        cutoff=cutoff,
                    )
                    threads_created += created["threads"]
                    messages_created += created["messages"]

                after = (
                    ((payload.get("paging") or {}).get("cursors") or {}).get("after")
                    if (payload.get("paging") or {}).get("next")
                    else None
                )
                if not after:
                    break
        finally:
            await client.close()

        logger.info(
            "omnichannel_backfill_complete",
            connection_id=str(connection_id),
            channel=connection.channel.value,
            conversations=conversations_seen,
            threads_created=threads_created,
            messages_created=messages_created,
        )
        return {
            "status": "ok",
            "conversations": conversations_seen,
            "threads_created": threads_created,
            "messages_created": messages_created,
        }

    async def _token_for(self, connection: ChannelConnection) -> str | None:
        if not (connection.encrypted_credentials and connection.credential_key_id):
            return None
        decrypted = await self.secrets_manager.decrypt(
            connection.encrypted_credentials, connection.credential_key_id
        )
        return decrypted.get("access_token") or None

    def _conversations_query(
        self, connection: ChannelConnection
    ) -> tuple[str, dict[str, Any]]:
        """Graph endpoint + params for this connection's conversations.

        Instagram conversations hang off the same node with an explicit
        ``platform=instagram``; Messenger is the default platform.
        """
        params: dict[str, Any] = {
            "fields": f"participants,updated_time,messages.limit(50){{{_MESSAGE_FIELDS}}}",
            "limit": _CONVERSATION_PAGE_SIZE,
        }
        node = connection.external_account_id
        if connection.channel == ChannelType.INSTAGRAM:
            # Instagram conversations live on the linked Page node, same as
            # sends — the IG user node rejects the call with (#3).
            params["platform"] = "instagram"
            node = connection.linked_page_id or node
        return f"{node}/conversations", params

    async def _ingest_conversation(
        self,
        connection: ChannelConnection,
        conversation: dict[str, Any],
        cutoff: datetime,
    ) -> dict[str, int]:
        messages = ((conversation.get("messages") or {}).get("data")) or []
        if not messages:
            return {"threads": 0, "messages": 0}

        participant = self._other_participant(connection, conversation, messages)
        if not participant or not participant.get("id"):
            return {"threads": 0, "messages": 0}
        participant_id = str(participant["id"])

        thread = await self.message_thread_repository.get_by_connection_and_participant(
            channel_connection_id=connection.id,
            external_participant_id=participant_id,
        )
        threads_created = 0
        messages_created = 0

        # Graph returns newest-first; replay oldest-first so the thread's
        # last_message_* fields end up on the genuinely newest message.
        for raw in reversed(messages):
            message_id = raw.get("id")
            sent_at = _parse_time(raw.get("created_time"))
            if not message_id or not sent_at or sent_at < cutoff:
                continue

            existing = await self.channel_message_repository.get_by_external_id(
                channel=connection.channel,
                external_message_id=message_id,
            )
            if existing:
                continue

            sender_id = str(((raw.get("from") or {}).get("id")) or "")
            is_outbound = sender_id == str(connection.external_account_id)
            body = raw.get("message") or None
            attachment_url, attachment_type = _first_attachment(raw)
            preview = body[:100] if body else "Attachment"

            if thread is None:
                thread = MessageThread(
                    tenant_id=connection.tenant_id,
                    store_id=connection.store_id,
                    channel=connection.channel,
                    channel_connection_id=connection.id,
                    external_participant_id=participant_id,
                    participant_name=participant.get("name"),
                    status=ThreadStatus.OPEN,
                    last_message_at=sent_at,
                    last_message_preview=preview,
                    # Historical messages are not "new" — never inflate the
                    # merchant's unread badge with a backfill.
                    unread_count=0,
                )
                await self.message_thread_repository.create(thread)
                threads_created += 1
            elif not thread.last_message_at or sent_at >= thread.last_message_at:
                if not thread.participant_name and participant.get("name"):
                    thread.participant_name = participant.get("name")
                thread.last_message_at = sent_at
                thread.last_message_preview = preview
                await self.message_thread_repository.update(thread)

            await self.channel_message_repository.create(
                ChannelMessage(
                    tenant_id=connection.tenant_id,
                    thread_id=thread.id,
                    direction=(
                        MessageDirection.OUTBOUND
                        if is_outbound
                        else MessageDirection.INBOUND
                    ),
                    channel=connection.channel,
                    external_message_id=message_id,
                    external_timestamp=sent_at,
                    sender_external_id=sender_id or None,
                    type=attachment_type,
                    body=body,
                    attachment_url=attachment_url,
                    status=(
                        MessageStatus.DELIVERED if is_outbound else MessageStatus.READ
                    ),
                    raw_payload={"backfill": True},
                )
            )
            messages_created += 1

        return {"threads": threads_created, "messages": messages_created}

    def _other_participant(
        self,
        connection: ChannelConnection,
        conversation: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """The customer side of the conversation (never our own account)."""
        own_id = str(connection.external_account_id)
        participants = ((conversation.get("participants") or {}).get("data")) or []
        for entry in participants:
            if str(entry.get("id")) != own_id:
                return entry
        # Older payloads omit participants; fall back to the first sender
        # that isn't us.
        for raw in messages:
            sender = raw.get("from") or {}
            if sender.get("id") and str(sender["id"]) != own_id:
                return sender
        return None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _first_attachment(raw: dict[str, Any]) -> tuple[str | None, MessageType]:
    attachments = ((raw.get("attachments") or {}).get("data")) or []
    if not attachments:
        return None, MessageType.TEXT
    first = attachments[0]
    url = (first.get("image_data") or {}).get("url") or first.get("file_url")
    mime = (first.get("mime_type") or "").lower()
    if mime.startswith("image/"):
        return url, MessageType.IMAGE
    if mime.startswith("video/"):
        return url, MessageType.VIDEO
    if mime.startswith("audio/"):
        return url, MessageType.AUDIO
    return url, MessageType.DOCUMENT if url else MessageType.TEXT

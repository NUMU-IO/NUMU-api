"""Ingest inbound message use case."""

from datetime import UTC, datetime
from uuid import UUID

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
from src.infrastructure.realtime.redis_pubsub import RealtimePublisher


class IngestInboundMessageUseCase:
    """Use case for ingesting inbound messages from webhooks."""

    def __init__(
        self,
        channel_connection_repository: ChannelConnectionRepository,
        message_thread_repository: MessageThreadRepository,
        channel_message_repository: ChannelMessageRepository,
        realtime_publisher: RealtimePublisher | None = None,
    ):
        self.channel_connection_repository = channel_connection_repository
        self.message_thread_repository = message_thread_repository
        self.channel_message_repository = channel_message_repository
        self.realtime_publisher = realtime_publisher

    async def execute(
        self,
        connection_id: UUID,
        external_message_id: str,
        sender_id: str,
        sender_name: str | None,
        message_type: str,
        body: str | None,
        attachment_url: str | None,
        external_timestamp: int,
    ) -> ChannelMessage:
        """Ingest an inbound message.

        Args:
            connection_id: The channel connection UUID
            external_message_id: External message ID from Meta
            sender_id: Sender's PSID/IGSID/phone
            sender_name: Sender's display name
            message_type: Type of message (text, image, etc.)
            body: Message text
            attachment_url: URL of attachment if any
            external_timestamp: Unix timestamp from Meta

        Returns:
            Created message entity
        """
        connection = await self.channel_connection_repository.get_by_id(connection_id)
        if not connection:
            raise ValueError("Connection not found")

        existing = await self.channel_message_repository.get_by_external_id(
            channel=connection.channel,
            external_message_id=external_message_id,
        )
        if existing:
            return existing

        thread = await self.message_thread_repository.get_by_connection_and_participant(
            channel_connection_id=connection_id,
            external_participant_id=sender_id,
        )

        timestamp = datetime.fromtimestamp(external_timestamp, tz=UTC)

        if not thread:
            thread = MessageThread(
                tenant_id=connection.tenant_id,
                store_id=connection.store_id,
                channel=connection.channel,
                channel_connection_id=connection_id,
                external_participant_id=sender_id,
                participant_name=sender_name,
                status=ThreadStatus.OPEN,
                last_message_at=timestamp,
                last_message_preview=body[:100] if body else "Attachment",
                unread_count=1,
            )
            await self.message_thread_repository.create(thread)
        else:
            thread.last_message_at = timestamp
            thread.last_message_preview = body[:100] if body else "Attachment"
            thread.unread_count = (thread.unread_count or 0) + 1
            await self.message_thread_repository.update(thread)

        msg_type = (
            MessageType(message_type)
            if message_type in [t.value for t in MessageType]
            else MessageType.TEXT
        )

        message = ChannelMessage(
            tenant_id=connection.tenant_id,
            thread_id=thread.id,
            direction=MessageDirection.INBOUND,
            channel=connection.channel,
            external_message_id=external_message_id,
            external_timestamp=timestamp,
            sender_external_id=sender_id,
            type=msg_type,
            body=body,
            attachment_url=attachment_url,
            status=MessageStatus.RECEIVED,
            raw_payload={},
        )
        await self.channel_message_repository.create(message)

        if self.realtime_publisher:
            await self.realtime_publisher.publish(
                channel=f"inbox:{connection.tenant_id}:{connection.store_id}",
                event={
                    "type": "new_message",
                    "thread_id": str(thread.id),
                    "message": {
                        "id": str(message.id),
                        "body": body,
                        "attachment_url": attachment_url,
                        "sender_name": sender_name,
                    },
                },
            )

        return message

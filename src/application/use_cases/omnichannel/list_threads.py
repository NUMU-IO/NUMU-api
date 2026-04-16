"""List threads use cases."""

from uuid import UUID

from src.application.dto.omnichannel import MessageThreadDTO
from src.core.interfaces.repositories.message_thread_repository import (
    MessageThreadRepository,
)


class ListThreadsUseCase:
    """Use case for listing message threads.

    Contract: GET /stores/{store_id}/inbox/threads
    Query params: ?channel=&status=&unread_only=&search=&cursor=&limit=50
    Response: { "data": { "threads": [ThreadDTO], "next_cursor": "string|null", "total_unread": 0 } }
    """

    def __init__(
        self,
        message_thread_repository: MessageThreadRepository,
    ):
        self.message_thread_repository = message_thread_repository

    async def execute(
        self,
        store_id: UUID,
        channel: str | None = None,
        status: str | None = None,
        unread_only: bool = False,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        """List threads for a store.

        Args:
            store_id: Store UUID (from route path)
            channel: Filter by channel type (facebook|instagram|whatsapp)
            status: Filter by status (open|resolved|spam)
            unread_only: Filter to only threads with unread messages
            search: Search participant name/phone
            cursor: Opaque cursor for pagination (base64 of last_external_timestamp)
            limit: Max results to return

        Returns:
            dict with keys: threads (list), next_cursor (str|None), total_unread (int)
        """
        from src.core.entities.channel_connection import ChannelType
        from src.core.entities.message_thread import ThreadStatus

        channel_filter = ChannelType(channel) if channel else None
        status_filter = ThreadStatus(status) if status else None

        threads = await self.message_thread_repository.list_by_store(
            store_id=store_id,
            channel=channel_filter,
            status=status_filter,
            unread_only=unread_only,
            search=search,
            cursor=cursor,
            limit=limit,
        )

        total_unread = await self.message_thread_repository.count_unread(store_id)

        return {
            "threads": [
                MessageThreadDTO(
                    id=t.id,
                    channel=t.channel.value,
                    participant_name=t.participant_name,
                    participant_avatar_url=t.participant_avatar_url,
                    participant_phone=t.participant_phone_e164,
                    status=t.status.value,
                    last_message_preview=t.last_message_preview,
                    last_message_at=t.last_message_at.isoformat()
                    if t.last_message_at
                    else None,
                    unread_count=t.unread_count,
                )
                for t in threads
            ],
            "next_cursor": None,
            "total_unread": total_unread,
        }


class GetThreadUseCase:
    """Use case for getting a single thread.

    Contract: GET /stores/{store_id}/inbox/threads/{thread_id}
    Response: { "data": ThreadDTO }
    """

    def __init__(
        self,
        message_thread_repository: MessageThreadRepository,
    ):
        self.message_thread_repository = message_thread_repository

    async def execute(self, thread_id: UUID) -> MessageThreadDTO | None:
        """Get a thread by ID.

        Args:
            thread_id: Thread UUID

        Returns:
            Thread DTO or None
        """
        thread = await self.message_thread_repository.get_by_id(thread_id)
        if not thread:
            return None

        return MessageThreadDTO(
            id=thread.id,
            channel=thread.channel.value,
            participant_name=thread.participant_name,
            participant_avatar_url=thread.participant_avatar_url,
            participant_phone=thread.participant_phone_e164,
            status=thread.status.value,
            last_message_preview=thread.last_message_preview,
            last_message_at=thread.last_message_at.isoformat()
            if thread.last_message_at
            else None,
            unread_count=thread.unread_count,
        )

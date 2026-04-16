"""List messages use case."""

from uuid import UUID

from src.application.dto.omnichannel import ChannelMessageDTO
from src.core.interfaces.repositories.channel_message_repository import (
    ChannelMessageRepository,
)


class ListMessagesUseCase:
    """Use case for listing messages in a thread.

    Contract: GET /stores/{store_id}/inbox/threads/{thread_id}/messages
    Query params: ?cursor=&limit=50
    Response: { "data": { "messages": [MessageDTO], "next_cursor": "string|null" } }
    """

    def __init__(
        self,
        channel_message_repository: ChannelMessageRepository,
    ) -> None:
        self.channel_message_repository = channel_message_repository

    async def execute(
        self,
        thread_id: UUID,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        """List messages for a thread.

        Args:
            thread_id: Thread UUID (from route path)
            cursor: Opaque cursor for pagination (base64 of last_external_timestamp)
            limit: Max results to return

        Returns:
            dict with keys: messages (list), next_cursor (str|None)
        """
        messages = await self.channel_message_repository.list_by_thread(
            thread_id=thread_id,
            cursor=cursor,
            limit=limit,
        )

        return {
            "messages": [
                ChannelMessageDTO(
                    id=m.id,
                    direction=m.direction.value,
                    type=m.type.value,
                    body=m.body,
                    attachment_url=m.attachment_url,
                    status=m.status.value,
                    created_at=m.created_at.isoformat(),
                    sender_name=m.sender_external_id,
                )
                for m in messages
            ],
            "next_cursor": None,
        }

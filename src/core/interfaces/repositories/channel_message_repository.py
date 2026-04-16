"""Channel message repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.channel_connection import ChannelType
from src.core.entities.channel_message import (
    ChannelMessage,
    MessageDirection,
    MessageStatus,
)
from src.core.interfaces.repositories.base import BaseRepository


class ChannelMessageRepository(BaseRepository[ChannelMessage]):
    """Repository interface for channel messages."""

    @abstractmethod
    async def get_by_external_id(
        self,
        channel: ChannelType,
        external_message_id: str,
    ) -> ChannelMessage | None:
        """Get message by external message ID (for idempotency)."""
        ...

    @abstractmethod
    async def list_by_thread(
        self,
        thread_id: UUID,
        cursor: str | None = None,
        limit: int = 100,
    ) -> list[ChannelMessage]:
        """List messages for a thread with cursor pagination.

        Args:
            thread_id: Thread UUID
            cursor: ISO timestamp of last external_timestamp (exclusive upper bound)
            limit: Max results
        """
        ...

    @abstractmethod
    async def update_status(
        self,
        message_id: UUID,
        status: MessageStatus,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ChannelMessage | None:
        """Update message delivery status."""
        ...

    @abstractmethod
    async def count_by_thread(self, thread_id: UUID) -> int:
        """Get total message count for a thread."""
        ...

    @abstractmethod
    async def get_latest_by_thread(
        self,
        thread_id: UUID,
        direction: MessageDirection | None = None,
    ) -> ChannelMessage | None:
        """Get the latest message in a thread."""
        ...

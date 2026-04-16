"""Message thread repository interface."""

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.channel_connection import ChannelType
from src.core.entities.message_thread import MessageThread, ThreadStatus
from src.core.interfaces.repositories.base import BaseRepository


class MessageThreadRepository(BaseRepository[MessageThread]):
    """Repository interface for message threads."""

    @abstractmethod
    async def get_by_connection_and_participant(
        self,
        channel_connection_id: UUID,
        external_participant_id: str,
    ) -> MessageThread | None:
        """Get thread by connection and participant."""
        ...

    @abstractmethod
    async def list_by_store(
        self,
        store_id: UUID,
        channel: ChannelType | None = None,
        status: ThreadStatus | None = None,
        unread_only: bool = False,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[MessageThread]:
        """List threads for a store with filters.

        Args:
            store_id: Store UUID
            channel: Filter by channel type
            status: Filter by thread status
            unread_only: Only threads with unread > 0
            search: Search participant name/phone
            cursor: Opaque cursor (base64 of last_external_timestamp)
            limit: Max results
        """
        ...

    @abstractmethod
    async def count_unread(self, store_id: UUID) -> int:
        """Get total unread count for a store."""
        ...

    @abstractmethod
    async def update_status(
        self,
        thread_id: UUID,
        status: ThreadStatus,
    ) -> MessageThread | None:
        """Update thread status."""
        ...

    @abstractmethod
    async def mark_read(self, thread_id: UUID) -> MessageThread | None:
        """Mark thread as read."""
        ...

    @abstractmethod
    async def increment_unread(self, thread_id: UUID) -> MessageThread | None:
        """Increment unread count for a thread."""
        ...

    @abstractmethod
    async def update_last_message(
        self,
        thread_id: UUID,
        message_preview: str,
        message_at: datetime,
    ) -> MessageThread | None:
        """Update last message preview and timestamp."""
        ...

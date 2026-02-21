"""MessageLog repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.message_log import (
    MessageDirection,
    MessageLog,
    MessageStatus,
)
from src.core.interfaces.repositories.base import BaseRepository


class IMessageLogRepository(BaseRepository[MessageLog]):
    """MessageLog repository interface."""

    @abstractmethod
    async def get_by_message_id(self, message_id: str) -> MessageLog | None:
        """Get a message log entry by its provider message ID."""
        ...

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        direction: MessageDirection | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[MessageLog]:
        """Get message logs for a store, optionally filtered by direction."""
        ...

    @abstractmethod
    async def get_by_phone(
        self,
        store_id: UUID,
        phone: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[MessageLog]:
        """Get message logs for a specific phone number within a store."""
        ...

    @abstractmethod
    async def update_status(
        self,
        message_id: str,
        status: MessageStatus,
        error_code: str | None = None,
    ) -> MessageLog | None:
        """Update the delivery status of a message by its provider message ID."""
        ...

"""EmailLog repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.email_log import EmailLog
from src.core.interfaces.repositories.base import BaseRepository


class IEmailLogRepository(BaseRepository[EmailLog]):
    """Repository contract for the transactional email audit trail."""

    @abstractmethod
    async def list_by_store(
        self,
        store_id: UUID,
        event_type: str | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[EmailLog]:
        """List email log entries for a store with optional filters."""
        ...

    @abstractmethod
    async def get_by_message_id(self, message_id: str) -> EmailLog | None:
        """Look up a log row by provider message id (used by webhook updates)."""
        ...

    @abstractmethod
    async def count_by_store(
        self,
        store_id: UUID,
        event_type: str | None = None,
        status: str | None = None,
    ) -> int:
        """Count log entries for a store, mirroring ``list_by_store`` filters."""
        ...

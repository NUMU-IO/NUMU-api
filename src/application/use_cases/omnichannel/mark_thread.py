"""Mark thread use cases."""

from uuid import UUID

from src.core.entities.message_thread import ThreadStatus
from src.core.interfaces.repositories.message_thread_repository import (
    MessageThreadRepository,
)


class MarkThreadReadUseCase:
    """Use case for marking a thread as read."""

    def __init__(
        self,
        message_thread_repository: MessageThreadRepository,
    ):
        self.message_thread_repository = message_thread_repository

    async def execute(self, thread_id: UUID) -> bool:
        """Mark thread as read.

        Args:
            thread_id: Thread UUID

        Returns:
            True if successful
        """
        result = await self.message_thread_repository.mark_read(thread_id)
        return result is not None


class ResolveThreadUseCase:
    """Use case for resolving a thread."""

    def __init__(
        self,
        message_thread_repository: MessageThreadRepository,
    ):
        self.message_thread_repository = message_thread_repository

    async def execute(self, thread_id: UUID) -> bool:
        """Mark thread as resolved.

        Args:
            thread_id: Thread UUID

        Returns:
            True if successful
        """
        result = await self.message_thread_repository.update_status(
            thread_id=thread_id,
            status=ThreadStatus.RESOLVED,
        )
        return result is not None

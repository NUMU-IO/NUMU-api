"""EmailTemplate repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.email_template import EmailTemplate
from src.core.interfaces.repositories.base import BaseRepository


class IEmailTemplateRepository(BaseRepository[EmailTemplate]):
    """Repository contract for per-store email template overrides."""

    @abstractmethod
    async def get_by_store_event_language(
        self,
        store_id: UUID,
        event_type: str,
        language: str,
    ) -> EmailTemplate | None:
        """Fetch the (possibly disabled) template matching the triple, if any."""
        ...

    @abstractmethod
    async def list_by_store(
        self,
        store_id: UUID,
        event_type: str | None = None,
        language: str | None = None,
        is_enabled: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[EmailTemplate]:
        """List templates for a store with optional filters and pagination."""
        ...

    @abstractmethod
    async def get_for_send(
        self,
        store_id: UUID,
        event_type: str,
        language: str,
    ) -> EmailTemplate | None:
        """Hot-path lookup used by the email-send pipeline.

        Implementations MUST return ``None`` when no matching row exists
        OR when the matching row has ``is_enabled = False``. Implementations
        SHOULD cache results — including negative lookups — for a short
        TTL to avoid hammering the DB on every notification.
        """
        ...

    @abstractmethod
    async def count_by_store(
        self,
        store_id: UUID,
        event_type: str | None = None,
        language: str | None = None,
        is_enabled: bool | None = None,
    ) -> int:
        """Count templates for a store, mirroring ``list_by_store`` filters."""
        ...

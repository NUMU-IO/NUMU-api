"""WhatsApp template repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.whatsapp_template import (
    TemplateCategory,
    TemplateStatus,
    WhatsAppTemplate,
)
from src.core.interfaces.repositories.base import BaseRepository


class WhatsAppTemplateRepository(BaseRepository[WhatsAppTemplate]):
    """Repository interface for WhatsApp templates."""

    @abstractmethod
    async def get_by_connection_and_name(
        self,
        channel_connection_id: UUID,
        name: str,
        language: str,
    ) -> WhatsAppTemplate | None:
        """Get template by connection, name, and language."""
        ...

    @abstractmethod
    async def list_by_connection(
        self,
        channel_connection_id: UUID,
        status: TemplateStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[WhatsAppTemplate]:
        """List templates for a connection with optional status filter."""
        ...

    @abstractmethod
    async def list_pending(
        self, skip: int = 0, limit: int = 100
    ) -> list[WhatsAppTemplate]:
        """List all pending templates across all connections (for polling)."""
        ...

    @abstractmethod
    async def update_status(
        self,
        template_id: UUID,
        status: TemplateStatus,
        rejection_reason: str | None = None,
    ) -> WhatsAppTemplate | None:
        """Update template status."""
        ...

    @abstractmethod
    async def list_by_store(
        self,
        store_id: UUID,
        category: TemplateCategory | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[WhatsAppTemplate]:
        """List templates for a store."""
        ...

"""Webhook event repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.webhook_event import WebhookEvent, WebhookProvider, WebhookStatus
from src.core.interfaces.repositories.base import BaseRepository


class WebhookEventRepository(BaseRepository[WebhookEvent]):
    """Repository interface for webhook events (audit/DLQ)."""

    @abstractmethod
    async def get_by_external_id(
        self,
        provider: WebhookProvider,
        external_id: str,
    ) -> WebhookEvent | None:
        """Get event by provider and external ID (for dedup)."""
        ...

    @abstractmethod
    async def list_by_provider(
        self,
        provider: WebhookProvider,
        status: WebhookStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[WebhookEvent]:
        """List events for a provider with optional status filter."""
        ...

    @abstractmethod
    async def list_failed(
        self, max_retries: int = 3, limit: int = 100
    ) -> list[WebhookEvent]:
        """List failed events that can be retried."""
        ...

    @abstractmethod
    async def update_status(
        self,
        event_id: UUID,
        status: WebhookStatus,
        error: str | None = None,
    ) -> WebhookEvent | None:
        """Update event status."""
        ...

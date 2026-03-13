"""Webhook repository interfaces."""

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.webhook import (
    WebhookDeliveryLog,
    WebhookEventType,
    WebhookSubscription,
)
from src.core.interfaces.repositories.base import BaseRepository


class IWebhookSubscriptionRepository(BaseRepository[WebhookSubscription]):
    """Webhook subscription repository interface."""

    @abstractmethod
    async def get_by_store(self, store_id: UUID) -> list[WebhookSubscription]:
        """Get all subscriptions for a store."""
        ...

    @abstractmethod
    async def get_active_for_event(
        self, store_id: UUID, event_type: WebhookEventType
    ) -> list[WebhookSubscription]:
        """Get active subscriptions for a store that subscribe to the given event."""
        ...

    @abstractmethod
    async def get_by_store_and_id(
        self, store_id: UUID, subscription_id: UUID
    ) -> WebhookSubscription | None:
        """Get a subscription by ID scoped to a store."""
        ...


class IWebhookDeliveryLogRepository(BaseRepository[WebhookDeliveryLog]):
    """Webhook delivery log repository interface."""

    @abstractmethod
    async def get_pending_retries(
        self, now: datetime, limit: int = 100
    ) -> list[WebhookDeliveryLog]:
        """Get delivery logs that are pending and due for retry."""
        ...

    @abstractmethod
    async def get_by_subscription(
        self,
        subscription_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[WebhookDeliveryLog]:
        """Get delivery logs for a specific subscription."""
        ...

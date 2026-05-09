"""Promotion domain events.

Published by the application-layer use cases when a promotion is
created, updated, or deleted. The infrastructure layer (step 04) wires
subscribers that:

* flush the Redis cache key `promotions:active:{store_id}:*`
* trigger the analytics rollup Celery job
"""

from uuid import UUID

from src.core.events.base import DomainEvent


class PromotionCreatedEvent(DomainEvent):
    """Emitted after a new promotion is persisted."""

    promotion_id: UUID
    store_id: UUID
    tenant_id: UUID
    surface: str
    actor_user_id: UUID | None = None


class PromotionUpdatedEvent(DomainEvent):
    """Emitted after a promotion is updated.

    Carries the new `version` so handlers can dedupe / detect stale
    cached writes.
    """

    promotion_id: UUID
    store_id: UUID
    tenant_id: UUID
    surface: str
    new_version: int
    actor_user_id: UUID | None = None


class PromotionDeletedEvent(DomainEvent):
    """Emitted when a promotion is hard-deleted.

    Hard delete is admin-only — the merchant flow uses `archive()` which
    publishes an `Updated` event instead.
    """

    promotion_id: UUID
    store_id: UUID
    tenant_id: UUID
    actor_user_id: UUID | None = None

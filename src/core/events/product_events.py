"""Product domain events."""

from uuid import UUID

from src.core.events.base import DomainEvent


class ProductCreatedEvent(DomainEvent):
    """Emitted when a new product is created."""

    product_id: UUID
    store_id: UUID
    name: str
    sku: str | None = None


class ProductUpdatedEvent(DomainEvent):
    """Emitted when a product is updated."""

    product_id: UUID
    store_id: UUID
    name: str


class ProductDeletedEvent(DomainEvent):
    """Emitted when a product is deleted."""

    product_id: UUID
    store_id: UUID

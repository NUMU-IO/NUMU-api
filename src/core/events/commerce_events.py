"""Customer, refund, shipment, stock and abandoned-checkout events.

Published once per committed change by ``infrastructure.events.commit_watch``
(the abandoned checkout by the abandoned-cart sweep). Each carries only what
its outgoing webhook sends.
"""

from uuid import UUID

from src.core.events.base import DomainEvent


class CustomerCreatedEvent(DomainEvent):
    store_id: UUID
    customer_id: UUID
    email: str | None = None
    phone: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    accepts_marketing: bool = False


class CustomerUpdatedEvent(CustomerCreatedEvent):
    pass


class RefundCreatedEvent(DomainEvent):
    store_id: UUID
    refund_id: UUID
    refund_number: str
    order_id: UUID
    status: str
    amount_cents: int
    currency: str


class RefundCompletedEvent(RefundCreatedEvent):
    pass


class ShipmentCreatedEvent(DomainEvent):
    store_id: UUID
    shipment_id: UUID
    order_id: UUID
    shipment_type: str
    carrier: str
    status: str
    tracking_number: str | None = None
    tracking_url: str | None = None


class ShipmentStatusChangedEvent(ShipmentCreatedEvent):
    previous_status: str


class InventoryLevelChangedEvent(DomainEvent):
    store_id: UUID
    product_id: UUID
    variant_id: UUID | None = None


class CheckoutAbandonedEvent(DomainEvent):
    store_id: UUID
    checkout_id: UUID | None = None
    customer_id: UUID | None = None
    items_count: int
    total_cents: int
    currency: str

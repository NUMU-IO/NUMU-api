"""Order domain events."""

from uuid import UUID

from src.core.events.base import DomainEvent


class OrderCreatedEvent(DomainEvent):
    """Emitted when a new order is created."""

    order_id: UUID
    order_number: str
    store_id: UUID
    customer_id: UUID
    total: float
    currency: str


class OrderPaidEvent(DomainEvent):
    """Emitted when an order's payment is confirmed."""

    order_id: UUID
    order_number: str
    store_id: UUID
    customer_id: UUID
    payment_id: str | None = None
    payment_method: str | None = None
    total: float


class OrderStatusChangedEvent(DomainEvent):
    """Emitted when an order's status changes.

    Carries all context needed by downstream handlers (email, activity log,
    webhook) so they don't need to re-query the database.
    """

    order_id: UUID
    order_number: str
    store_id: UUID
    store_name: str
    customer_id: UUID
    customer_email: str | None = None
    customer_phone: str | None = None
    customer_name: str | None = None
    previous_status: str
    new_status: str
    reason: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    carrier: str | None = None
    language: str = "ar"
    # Notification preferences (from customer.metadata)
    email_prefs: dict = {}
    whatsapp_prefs: dict = {}

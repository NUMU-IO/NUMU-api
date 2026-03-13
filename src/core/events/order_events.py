"""Order domain events."""

from uuid import UUID

from src.core.events.base import DomainEvent


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
    language: str = "en"
    # Notification preferences (from customer.metadata)
    email_prefs: dict = {}
    whatsapp_prefs: dict = {}

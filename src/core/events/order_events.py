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


class OrderPaymentReversedEvent(DomainEvent):
    """Emitted when a merchant un-marks a manually recorded payment.

    The mirror of ``OrderPaidEvent`` for the side effects that CAN be
    undone: wallet commission, the ETA invoice, the activity timeline and
    the merchant feed. Customer messages already sent are not recalled.
    """

    order_id: UUID
    order_number: str
    store_id: UUID
    customer_id: UUID | None = None
    amount_cents: int = 0
    reason: str | None = None
    actor_user_id: UUID | None = None


class OrderPartiallyAcceptedEvent(DomainEvent):
    """Emitted when a delivered order had some pieces returned at the door.

    ``lines`` carries ``{order_line_index, returned_quantity, value_cents,
    product_id, variant_id}`` per returned line.
    """

    order_id: UUID
    order_number: str
    store_id: UUID
    customer_id: UUID | None = None
    returned_value_cents: int = 0
    collected_total_cents: int = 0
    refund_due_cents: int = 0
    lines: list[dict] = []
    reason: str | None = None
    actor_user_id: UUID | None = None

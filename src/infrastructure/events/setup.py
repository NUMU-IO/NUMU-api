"""Event bus factory - creates and wires the application event bus.

Called once at application startup to register all event handlers.
"""

from src.core.events.base import EventBus
from src.core.events.order_events import (
    OrderCreatedEvent,
    OrderPaidEvent,
    OrderStatusChangedEvent,
)
from src.core.events.product_events import (
    ProductCreatedEvent,
    ProductDeletedEvent,
    ProductUpdatedEvent,
)
from src.infrastructure.events.handlers.activity_log_handler import handle_activity_log
from src.infrastructure.events.handlers.email_notification_handler import (
    handle_email_notification,
)
from src.infrastructure.events.handlers.shipment_handler import (
    handle_order_status_for_shipment,
)
from src.infrastructure.events.handlers.webhook_handler import (
    handle_webhook_order_created,
    handle_webhook_order_paid,
    handle_webhook_order_status_changed,
    handle_webhook_product_created,
    handle_webhook_product_deleted,
    handle_webhook_product_updated,
)
from src.infrastructure.events.handlers.whatsapp_notification_handler import (
    handle_whatsapp_notification,
)

# Module-level singleton
_event_bus: EventBus | None = None


def create_event_bus() -> EventBus:
    "Create and wire the global event bus (idempotent)."
    global _event_bus
    if _event_bus is not None:
        return _event_bus

    bus = EventBus()

    # Order status change - notifications + activity log + webhook
    bus.subscribe(OrderStatusChangedEvent, handle_email_notification)
    bus.subscribe(OrderStatusChangedEvent, handle_whatsapp_notification)
    bus.subscribe(OrderStatusChangedEvent, handle_activity_log)
    bus.subscribe(OrderStatusChangedEvent, handle_webhook_order_status_changed)

    # Auto-create shipment on order confirmation
    bus.subscribe(OrderStatusChangedEvent, handle_order_status_for_shipment)

    # Order lifecycle webhooks
    bus.subscribe(OrderCreatedEvent, handle_webhook_order_created)
    bus.subscribe(OrderPaidEvent, handle_webhook_order_paid)

    # Product webhooks
    bus.subscribe(ProductCreatedEvent, handle_webhook_product_created)
    bus.subscribe(ProductUpdatedEvent, handle_webhook_product_updated)
    bus.subscribe(ProductDeletedEvent, handle_webhook_product_deleted)

    _event_bus = bus
    return bus


def get_event_bus() -> EventBus:
    "Get the global event bus, creating it if needed."
    if _event_bus is None:
        return create_event_bus()
    return _event_bus

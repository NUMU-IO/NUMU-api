"""Event bus factory — creates and wires the application event bus.

Called once at application startup to register all event handlers.
"""

from src.core.events.base import EventBus
from src.core.events.order_events import OrderStatusChangedEvent
from src.infrastructure.events.handlers.activity_log_handler import handle_activity_log
from src.infrastructure.events.handlers.email_notification_handler import (
    handle_email_notification,
)
from src.infrastructure.events.handlers.webhook_handler import handle_webhook
from src.infrastructure.events.handlers.whatsapp_notification_handler import (
    handle_whatsapp_notification,
)

# Module-level singleton
_event_bus: EventBus | None = None


def create_event_bus() -> EventBus:
    """Create and wire the global event bus (idempotent)."""
    global _event_bus
    if _event_bus is not None:
        return _event_bus

    bus = EventBus()

    # Order status change handlers (all run concurrently)
    bus.subscribe(OrderStatusChangedEvent, handle_email_notification)
    bus.subscribe(OrderStatusChangedEvent, handle_whatsapp_notification)
    bus.subscribe(OrderStatusChangedEvent, handle_activity_log)
    bus.subscribe(OrderStatusChangedEvent, handle_webhook)

    _event_bus = bus
    return bus


def get_event_bus() -> EventBus:
    """Get the global event bus, creating it if needed."""
    if _event_bus is None:
        return create_event_bus()
    return _event_bus

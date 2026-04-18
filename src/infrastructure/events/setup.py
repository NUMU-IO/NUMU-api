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
from src.core.events.staff_events import (
    AccessRequestApprovedEvent,
    AccessRequestCreatedEvent,
    AccessRequestDeniedEvent,
    PermissionOverrideClearedEvent,
    PermissionOverrideSetEvent,
    StaffActivatedEvent,
    StaffInvitedEvent,
    StaffRemovedEvent,
    StaffRoleAssignedEvent,
    StaffRoleRevokedEvent,
    TemporaryAccessGrantedEvent,
    TemporaryAccessRevokedEvent,
)
from src.infrastructure.events.handlers.activity_log_handler import handle_activity_log
from src.infrastructure.events.handlers.email_notification_handler import (
    handle_email_notification,
)
from src.infrastructure.events.handlers.shipment_handler import (
    handle_order_status_for_shipment,
)
from src.infrastructure.events.handlers.staff_event_handlers import (
    handle_access_request_approved,
    handle_access_request_created,
    handle_access_request_denied,
    handle_override_cleared,
    handle_override_set,
    handle_staff_activated,
    handle_staff_invited,
    handle_staff_removed,
    handle_staff_role_assigned,
    handle_staff_role_revoked,
    handle_temporary_access_granted,
    handle_temporary_access_revoked,
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

    # Staff events - invalidate cache + activity log + notifications
    bus.subscribe(StaffInvitedEvent, handle_staff_invited)
    bus.subscribe(StaffActivatedEvent, handle_staff_activated)
    bus.subscribe(StaffRoleAssignedEvent, handle_staff_role_assigned)
    bus.subscribe(StaffRoleRevokedEvent, handle_staff_role_revoked)
    bus.subscribe(StaffRemovedEvent, handle_staff_removed)
    bus.subscribe(PermissionOverrideSetEvent, handle_override_set)
    bus.subscribe(PermissionOverrideClearedEvent, handle_override_cleared)
    bus.subscribe(AccessRequestCreatedEvent, handle_access_request_created)
    bus.subscribe(AccessRequestApprovedEvent, handle_access_request_approved)
    bus.subscribe(AccessRequestDeniedEvent, handle_access_request_denied)
    bus.subscribe(TemporaryAccessGrantedEvent, handle_temporary_access_granted)
    bus.subscribe(TemporaryAccessRevokedEvent, handle_temporary_access_revoked)

    _event_bus = bus
    return bus


def get_event_bus() -> EventBus:
    "Get the global event bus, creating it if needed."
    if _event_bus is None:
        return create_event_bus()
    return _event_bus

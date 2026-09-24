"""Webhook domain entities."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.core.entities.base import BaseEntity


class WebhookEventType(StrEnum):
    """Every event that is actually published.

    This list was once three times longer: thirteen names — returns, refunds,
    customer and review events — were declared and accepted at subscribe time,
    but nothing ever published them. A merchant could subscribe to
    ``return.approved``, get a 201, and wait forever. Nothing publishes an
    event because it appears here, so a name that no handler dispatches is a
    promise the platform silently breaks; they are gone until their domain
    events exist.

    ``order.status_changed`` reports the order's status and tracking number;
    ``shipment.*`` reports each shipment's own carrier status.
    """

    ORDER_CREATED = "order.created"
    ORDER_PAID = "order.paid"
    ORDER_STATUS_CHANGED = "order.status_changed"
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    PRODUCT_DELETED = "product.deleted"
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    REFUND_CREATED = "refund.created"
    REFUND_COMPLETED = "refund.completed"
    SHIPMENT_CREATED = "shipment.created"
    SHIPMENT_STATUS_CHANGED = "shipment.status_changed"
    INVENTORY_LEVEL_CHANGED = "inventory.level_changed"
    CHECKOUT_ABANDONED = "checkout.abandoned"
    #: Delivered only by the test endpoint, never by an event. Not
    #: subscribable — see ``SUBSCRIBABLE_EVENT_TYPES``.
    PING = "webhook.ping"


#: What a merchant may subscribe to. PING is deliverable but not subscribable.
SUBSCRIBABLE_EVENT_TYPES = tuple(
    e for e in WebhookEventType if e is not WebhookEventType.PING
)


class WebhookDeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    EXHAUSTED = "exhausted"


class WebhookSubscription(BaseEntity):
    """A merchant-registered endpoint that receives event payloads."""

    store_id: UUID
    tenant_id: UUID
    url: str
    events: list[WebhookEventType]
    secret: str
    is_active: bool = True
    description: str | None = None
    #: Owned by a Partner App: signed with the app's client secret (``secret``
    #: is empty), never shown in or editable from the merchant's own list.
    app_installation_id: UUID | None = None


class WebhookDeliveryLog(BaseEntity):
    """Tracks a single webhook delivery attempt (and its retries)."""

    subscription_id: UUID | None  # nullable: survives subscription deletion
    store_id: UUID
    tenant_id: UUID
    event_type: WebhookEventType
    event_id: UUID
    payload: dict
    status: WebhookDeliveryStatus = WebhookDeliveryStatus.PENDING
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_status_code: int | None = None
    last_response_body: str | None = None
    last_error: str | None = None
    exhausted_at: datetime | None = None

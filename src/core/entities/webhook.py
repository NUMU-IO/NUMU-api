"""Webhook domain entities."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.core.entities.base import BaseEntity


class WebhookEventType(StrEnum):
    ORDER_CREATED = "order.created"
    ORDER_PAID = "order.paid"
    ORDER_STATUS_CHANGED = "order.status_changed"
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    PRODUCT_DELETED = "product.deleted"
    # Phase 3 additions — additive, no migration needed (the column is
    # a Postgres array of strings and the dispatcher fans out by name).
    # Order/refund/return lifecycle so merchants can wire ERPs that
    # care about post-purchase events.
    ORDER_FULFILLED = "order.fulfilled"
    ORDER_REFUNDED = "order.refunded"
    REFUND_PROCESSED = "refund.processed"
    RETURN_REQUESTED = "return.requested"
    RETURN_APPROVED = "return.approved"
    RETURN_REJECTED = "return.rejected"
    RETURN_RECEIVED = "return.received"
    RETURN_COMPLETED = "return.completed"
    # Customer profile changes — useful for CRM sync.
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    # Phase 3.4/3.5 — review + back-in-stock signals so merchants can
    # forward to managed moderation (e.g. Perspective API) or build
    # custom out-of-stock dashboards.
    REVIEW_HELD = "review.held"
    REVIEW_PUBLISHED = "review.published"
    BACK_IN_STOCK_NOTIFIED = "back_in_stock.notified"


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

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

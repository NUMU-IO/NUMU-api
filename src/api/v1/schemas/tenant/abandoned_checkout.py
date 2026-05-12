"""Abandoned checkout Pydantic schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AbandonedCheckoutLineItem(BaseModel):
    """Line item snapshot stored on an abandoned checkout."""

    product_id: UUID | None = None
    product_name: str | None = None
    variant_id: UUID | None = None
    variant_name: str | None = None
    sku: str | None = None
    quantity: int = 1
    unit_price: int = 0
    total_price: int = 0


class AbandonedCheckoutResponse(BaseModel):
    """Single abandoned checkout entry."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    customer_id: UUID | None = None
    email: str | None = None
    phone: str | None = None
    line_items: list[AbandonedCheckoutLineItem] = Field(default_factory=list)
    shipping_address: dict | None = None
    subtotal: int
    shipping_cost: int
    tax_amount: int
    discount_amount: int
    total: int
    currency: str
    coupon_code: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    last_activity_at: datetime
    abandoned_at: datetime | None = None
    recovered_at: datetime | None = None
    recovery_email_sent_at: datetime | None = None
    recovered_order_id: UUID | None = None
    item_count: int = 0
    created_at: datetime
    updated_at: datetime


class AbandonedCheckoutListResponse(BaseModel):
    """Paginated abandoned-checkout feed."""

    items: list[AbandonedCheckoutResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class SendRecoveryEmailResponse(BaseModel):
    """Result of a recovery-email send attempt."""

    checkout_id: UUID
    email: str
    sent_at: datetime

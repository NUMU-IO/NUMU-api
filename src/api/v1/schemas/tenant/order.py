"""Order Pydantic schemas."""

from uuid import UUID

from pydantic import BaseModel, Field


class OrderLineItemRequest(BaseModel):
    """Order line item request schema."""

    product_id: UUID
    product_name: str = Field(..., min_length=1, max_length=255)
    variant_id: UUID | None = None
    variant_name: str | None = None
    sku: str | None = None
    quantity: int = Field(default=1, ge=1)
    unit_price: int = Field(..., ge=0, description="Price in cents")


class OrderAddressRequest(BaseModel):
    """Order shipping/billing address request schema."""

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    address_line1: str = Field(..., min_length=1, max_length=255)
    address_line2: str | None = Field(None, max_length=255)
    city: str = Field(..., min_length=1, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: str = Field(..., min_length=2, max_length=100)
    phone: str | None = Field(None, max_length=20)


class CreateOrderRequest(BaseModel):
    """Create order request schema."""

    customer_id: UUID
    line_items: list[OrderLineItemRequest] = Field(..., min_length=1)
    shipping_address: OrderAddressRequest
    billing_address: OrderAddressRequest | None = None
    shipping_cost: int = Field(default=0, ge=0, description="Shipping cost in cents")
    tax_amount: int = Field(default=0, ge=0, description="Tax amount in cents")
    discount_amount: int = Field(default=0, ge=0, description="Discount in cents")
    currency: str = Field(default="EGP", max_length=3)
    payment_method: str | None = None
    shipping_method: str | None = None
    customer_notes: str | None = None


class UpdateOrderRequest(BaseModel):
    """Update order request schema."""

    shipping_address: OrderAddressRequest | None = None
    billing_address: OrderAddressRequest | None = None
    shipping_cost: int | None = Field(None, ge=0)
    tax_amount: int | None = Field(None, ge=0)
    discount_amount: int | None = Field(None, ge=0)
    payment_method: str | None = None
    shipping_method: str | None = None
    tracking_number: str | None = None
    notes: str | None = None
    customer_notes: str | None = None


class UpdateOrderStatusRequest(BaseModel):
    """Update order status request schema."""

    status: str = Field(..., description="New order status")
    reason: str | None = Field(None, description="Reason for status change")


class OrderLineItemResponse(BaseModel):
    """Order line item response schema."""

    product_id: str
    product_name: str
    variant_id: str | None
    variant_name: str | None
    sku: str | None
    quantity: int
    unit_price: int
    total_price: int

    class Config:
        from_attributes = True


class OrderAddressResponse(BaseModel):
    """Order address response schema."""

    first_name: str
    last_name: str
    full_name: str
    address_line1: str
    address_line2: str | None
    city: str
    state: str | None
    postal_code: str | None
    country: str
    phone: str | None

    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    """Full order response schema."""

    id: str
    store_id: str
    customer_id: str
    order_number: str
    line_items: list[OrderLineItemResponse]
    shipping_address: OrderAddressResponse
    billing_address: OrderAddressResponse | None
    status: str
    payment_status: str
    fulfillment_status: str
    subtotal: int
    shipping_cost: int
    tax_amount: int
    discount_amount: int
    total: int
    currency: str
    payment_method: str | None
    payment_id: str | None
    shipping_method: str | None
    tracking_number: str | None
    tracking_url: str | None
    notes: str | None
    customer_notes: str | None
    item_count: int
    is_paid: bool
    can_be_cancelled: bool
    cancelled_at: str | None
    paid_at: str | None
    fulfilled_at: str | None
    shipped_at: str | None
    delivered_at: str | None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class OrderListItemResponse(BaseModel):
    """Order list item response schema (summary)."""

    id: str
    order_number: str
    customer_id: str
    customer_name: str | None = None
    status: str
    payment_status: str
    fulfillment_status: str
    total: int
    currency: str
    item_count: int
    payment_method: str | None
    created_at: str

    class Config:
        from_attributes = True


# ============================================================================
# Enriched / Timeline / Bulk schemas
# ============================================================================


class OrderTimelineEvent(BaseModel):
    """Single event in an order's timeline."""

    timestamp: str
    status: str
    description: str
    actor: str | None = None


class OrderTimelineResponse(BaseModel):
    """Order timeline response."""

    order_id: str
    order_number: str
    events: list[OrderTimelineEvent]

    class Config:
        from_attributes = True


class OrderDetailEnrichedResponse(OrderResponse):
    """Enriched order response with customer info and timeline.

    Extends the base OrderResponse with additional context useful
    for the store-owner dashboard detail view.
    """

    customer_name: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    timeline: list[OrderTimelineEvent] = []

    class Config:
        from_attributes = True


class BulkUpdateOrderStatusRequest(BaseModel):
    """Bulk update order status request."""

    order_ids: list[UUID] = Field(..., min_length=1, max_length=100)
    status: str = Field(..., description="New order status")
    reason: str | None = Field(None, description="Reason for status change")


class BulkUpdateOrderStatusResponse(BaseModel):
    """Bulk update order status response."""

    updated: int
    failed: int = 0
    errors: list[dict] = Field(default_factory=list)

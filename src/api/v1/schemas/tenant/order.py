"""Order Pydantic schemas."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.api.dependencies.sanitization import SanitizedStr


class OrderLineItemRequest(BaseModel):
    """Order line item request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "product_id": "550e8400-e29b-41d4-a716-446655440000",
                "product_name": "Egyptian Cotton T-Shirt",
                "quantity": 2,
                "unit_price": 25000,
            }
        }
    )

    product_id: UUID = Field(description="Product UUID")
    product_name: SanitizedStr = Field(
        ..., min_length=1, max_length=255, description="Product name at time of order"
    )
    variant_id: UUID | None = Field(None, description="Product variant UUID")
    variant_name: str | None = Field(
        None, description="Variant label (e.g. 'Large / Blue')"
    )
    sku: str | None = Field(None, description="SKU at time of order")
    quantity: int = Field(default=1, ge=1, description="Quantity ordered")
    unit_price: int = Field(..., ge=0, description="Price per unit in cents")


class OrderAddressRequest(BaseModel):
    """Order shipping/billing address request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Ahmed",
                "last_name": "Hassan",
                "address_line1": "12 Tahrir Square",
                "city": "Cairo",
                "country": "Egypt",
                "phone": "+201234567890",
            }
        }
    )

    first_name: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="Recipient first name"
    )
    last_name: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="Recipient last name"
    )
    address_line1: SanitizedStr = Field(
        ..., min_length=1, max_length=255, description="Street address line 1"
    )
    address_line2: SanitizedStr | None = Field(
        None, max_length=255, description="Apartment, suite, floor, etc."
    )
    city: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="City name"
    )
    state: str | None = Field(None, max_length=100, description="State or governorate")
    postal_code: str | None = Field(
        None, max_length=20, description="Postal / ZIP code"
    )
    country: str = Field(
        ..., min_length=2, max_length=100, description="Country name or ISO code"
    )
    phone: str | None = Field(None, max_length=20, description="Contact phone number")
    # Geolocation fields captured from the storefront map picker. All optional
    # to preserve backward-compat with clients that don't send them.
    latitude: float | None = Field(
        None, ge=-90, le=90, description="Delivery point latitude (WGS84)"
    )
    longitude: float | None = Field(
        None, ge=-180, le=180, description="Delivery point longitude (WGS84)"
    )
    location_accuracy: float | None = Field(
        None, ge=0, description="GPS accuracy radius in meters"
    )
    location_source: str | None = Field(
        None,
        max_length=20,
        description="How the location was captured: 'gps' | 'manual_pin'",
    )
    geocoded_address: str | None = Field(
        None,
        max_length=500,
        description="Provider-normalized formatted address (from reverse geocoding)",
    )


class CreateOrderRequest(BaseModel):
    """Create order request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "customer_id": "660e8400-e29b-41d4-a716-446655440000",
                "line_items": [
                    {
                        "product_id": "550e8400-e29b-41d4-a716-446655440000",
                        "product_name": "Egyptian Cotton T-Shirt",
                        "quantity": 2,
                        "unit_price": 25000,
                    }
                ],
                "shipping_address": {
                    "first_name": "Ahmed",
                    "last_name": "Hassan",
                    "address_line1": "12 Tahrir Square",
                    "city": "Cairo",
                    "country": "Egypt",
                },
                "shipping_cost": 5000,
                "currency": "EGP",
                "payment_method": "cod",
            }
        }
    )

    customer_id: UUID = Field(description="Customer UUID")
    line_items: list[OrderLineItemRequest] = Field(
        ..., min_length=1, description="Order line items (at least one required)"
    )
    shipping_address: OrderAddressRequest = Field(description="Shipping address")
    billing_address: OrderAddressRequest | None = Field(
        None, description="Billing address; defaults to shipping address if omitted"
    )
    shipping_cost: int = Field(default=0, ge=0, description="Shipping cost in cents")
    tax_amount: int = Field(default=0, ge=0, description="Tax amount in cents")
    discount_amount: int = Field(
        default=0, ge=0, description="Discount amount in cents"
    )
    currency: str = Field(
        default="EGP", max_length=3, description="ISO 4217 currency code"
    )
    payment_method: str | None = Field(
        None, description="Payment method: cod, paymob, fawry, etc."
    )
    shipping_method: str | None = Field(
        None, description="Shipping method: bosta, pickup, etc."
    )
    customer_notes: SanitizedStr | None = Field(
        None, description="Notes from the customer"
    )


class UpdateOrderRequest(BaseModel):
    """Update order request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tracking_number": "BOSTA-12345",
                "notes": "Called customer to confirm delivery time",
            }
        }
    )

    shipping_address: OrderAddressRequest | None = Field(
        None, description="Updated shipping address"
    )
    billing_address: OrderAddressRequest | None = Field(
        None, description="Updated billing address"
    )
    shipping_cost: int | None = Field(None, ge=0, description="Shipping cost in cents")
    tax_amount: int | None = Field(None, ge=0, description="Tax amount in cents")
    discount_amount: int | None = Field(None, ge=0, description="Discount in cents")
    payment_method: str | None = Field(None, description="Payment method")
    shipping_method: str | None = Field(None, description="Shipping method")
    tracking_number: str | None = Field(None, description="Shipment tracking number")
    notes: SanitizedStr | None = Field(
        None, description="Internal notes for the merchant"
    )
    customer_notes: SanitizedStr | None = Field(
        None, description="Notes from the customer"
    )


class UpdateOrderStatusRequest(BaseModel):
    """Update order status request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "shipped",
                "reason": "Items picked and handed to Bosta courier",
            }
        }
    )

    status: str = Field(..., description="New order status")
    reason: str | None = Field(None, description="Reason for status change")


class OrderLineItemResponse(BaseModel):
    """Order line item response schema."""

    model_config = ConfigDict(from_attributes=True)

    product_id: str = Field(description="Product UUID")
    product_name: str = Field(description="Product name")
    variant_id: str | None = Field(description="Variant UUID")
    variant_name: str | None = Field(description="Variant label")
    sku: str | None = Field(description="SKU")
    quantity: int = Field(description="Quantity ordered")
    unit_price: int = Field(description="Unit price in cents")
    total_price: int = Field(description="Line total in cents (unit_price * quantity)")


class OrderAddressResponse(BaseModel):
    """Order address response schema."""

    model_config = ConfigDict(from_attributes=True)

    first_name: str = Field(description="First name")
    last_name: str = Field(description="Last name")
    full_name: str = Field(description="Concatenated full name")
    address_line1: str = Field(description="Address line 1")
    address_line2: str | None = Field(description="Address line 2")
    city: str = Field(description="City")
    state: str | None = Field(description="State or governorate")
    postal_code: str | None = Field(description="Postal code")
    country: str = Field(description="Country")
    phone: str | None = Field(description="Phone number")
    latitude: float | None = Field(None, description="Delivery point latitude")
    longitude: float | None = Field(None, description="Delivery point longitude")
    location_accuracy: float | None = Field(
        None, description="GPS accuracy radius in meters"
    )
    location_source: str | None = Field(
        None, description="Location capture source: 'gps' | 'manual_pin'"
    )
    geocoded_address: str | None = Field(
        None, description="Provider-normalized formatted address"
    )


class OrderResponse(BaseModel):
    """Full order response schema."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "770e8400-e29b-41d4-a716-446655440000",
                "store_id": "660e8400-e29b-41d4-a716-446655440000",
                "customer_id": "550e8400-e29b-41d4-a716-446655440000",
                "order_number": "ORD-1001",
                "status": "pending",
                "payment_status": "unpaid",
                "fulfillment_status": "unfulfilled",
                "subtotal": 50000,
                "shipping_cost": 5000,
                "tax_amount": 7700,
                "discount_amount": 0,
                "total": 62700,
                "currency": "EGP",
                "payment_method": "cod",
                "item_count": 2,
                "is_paid": False,
                "can_be_cancelled": True,
                "created_at": "2025-01-20T14:30:00Z",
                "updated_at": "2025-01-20T14:30:00Z",
            }
        },
    )

    id: str = Field(description="Order UUID")
    store_id: str = Field(description="Store UUID")
    customer_id: str = Field(description="Customer UUID")
    order_number: str = Field(description="Human-readable order number")
    line_items: list[OrderLineItemResponse] = Field(description="Line items")
    shipping_address: OrderAddressResponse = Field(description="Shipping address")
    billing_address: OrderAddressResponse | None = Field(description="Billing address")
    status: str = Field(
        description="Order status: pending, confirmed, shipped, delivered, cancelled"
    )
    payment_status: str = Field(description="Payment status: unpaid, paid, refunded")
    fulfillment_status: str = Field(
        description="Fulfillment status: unfulfilled, fulfilled, partial"
    )
    subtotal: int = Field(description="Subtotal in cents")
    shipping_cost: int = Field(description="Shipping cost in cents")
    tax_amount: int = Field(description="Tax amount in cents")
    discount_amount: int = Field(description="Discount amount in cents")
    total: int = Field(description="Grand total in cents")
    currency: str = Field(description="ISO 4217 currency code")
    payment_method: str | None = Field(description="Payment method used")
    payment_id: str | None = Field(description="External payment transaction ID")
    shipping_method: str | None = Field(description="Shipping method used")
    tracking_number: str | None = Field(description="Shipment tracking number")
    tracking_url: str | None = Field(description="Shipment tracking URL")
    notes: str | None = Field(description="Internal merchant notes")
    customer_notes: str | None = Field(description="Customer-provided notes")
    item_count: int = Field(description="Total number of items")
    is_paid: bool = Field(description="Whether the order has been paid")
    can_be_cancelled: bool = Field(
        description="Whether the order can still be cancelled"
    )
    cancelled_at: str | None = Field(description="ISO 8601 cancellation timestamp")
    paid_at: str | None = Field(description="ISO 8601 payment timestamp")
    fulfilled_at: str | None = Field(description="ISO 8601 fulfilment timestamp")
    shipped_at: str | None = Field(description="ISO 8601 shipment timestamp")
    delivered_at: str | None = Field(description="ISO 8601 delivery timestamp")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class OrderListItemResponse(BaseModel):
    """Order list item response schema (summary)."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Order UUID")
    order_number: str = Field(description="Human-readable order number")
    customer_id: str = Field(description="Customer UUID")
    customer_name: str | None = Field(None, description="Customer full name")
    status: str = Field(description="Order status")
    payment_status: str = Field(description="Payment status")
    fulfillment_status: str = Field(description="Fulfillment status")
    total: int = Field(description="Grand total in cents")
    currency: str = Field(description="Currency code")
    item_count: int = Field(description="Total items")
    payment_method: str | None = Field(description="Payment method")
    created_at: str = Field(description="ISO 8601 creation timestamp")


# ============================================================================
# Enriched / Timeline / Bulk schemas
# ============================================================================


class OrderTimelineEvent(BaseModel):
    """Single event in an order's timeline."""

    timestamp: str = Field(description="ISO 8601 event timestamp")
    status: str = Field(description="Status at this point")
    description: str = Field(description="Human-readable event description")
    actor: str | None = Field(None, description="Who triggered this event")


class OrderTimelineResponse(BaseModel):
    """Order timeline response."""

    model_config = ConfigDict(from_attributes=True)

    order_id: str = Field(description="Order UUID")
    order_number: str = Field(description="Order number")
    events: list[OrderTimelineEvent] = Field(
        description="Timeline events in chronological order"
    )


class OrderDetailEnrichedResponse(OrderResponse):
    """Enriched order response with customer info and timeline.

    Extends the base OrderResponse with additional context useful
    for the store-owner dashboard detail view.
    """

    model_config = ConfigDict(from_attributes=True)

    customer_name: str | None = Field(None, description="Customer full name")
    customer_email: str | None = Field(None, description="Customer email")
    customer_phone: str | None = Field(None, description="Customer phone")
    timeline: list[OrderTimelineEvent] = Field(
        default_factory=list, description="Order lifecycle events"
    )


class BulkUpdateOrderStatusRequest(BaseModel):
    """Bulk update order status request."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "order_ids": [
                    "770e8400-e29b-41d4-a716-446655440000",
                    "880e8400-e29b-41d4-a716-446655440000",
                ],
                "status": "shipped",
                "reason": "Batch shipment via Bosta",
            }
        }
    )

    order_ids: list[UUID] = Field(
        ..., min_length=1, max_length=100, description="List of order UUIDs to update"
    )
    status: str = Field(..., description="New order status")
    reason: str | None = Field(None, description="Reason for status change")


class BulkUpdateOrderStatusResponse(BaseModel):
    """Bulk update order status response."""

    updated: int = Field(description="Number of orders successfully updated")
    failed: int = Field(default=0, description="Number of orders that failed to update")
    errors: list[dict] = Field(
        default_factory=list, description="Error details for failed updates"
    )

"""Coupon Pydantic schemas for store management."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CreateCouponRequest(BaseModel):
    """Create coupon request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "SUMMER25",
                "coupon_type": "percentage",
                "value": "25.00",
                "min_order_amount": "100.00",
                "max_discount_amount": "50.00",
                "usage_limit": 100,
                "valid_from": "2025-06-01T00:00:00Z",
                "valid_until": "2025-08-31T23:59:59Z",
            }
        }
    )

    code: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Unique coupon code (case-insensitive)",
    )
    coupon_type: str = Field(
        ..., description="Discount type: percentage, fixed, or free_shipping"
    )
    value: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Discount value — percentage (0-100) or fixed amount",
    )
    min_order_amount: Decimal | None = Field(
        None, ge=0, description="Minimum order subtotal required to apply the coupon"
    )
    max_discount_amount: Decimal | None = Field(
        None, ge=0, description="Maximum discount cap (for percentage coupons)"
    )
    usage_limit: int | None = Field(
        None, ge=1, description="Maximum number of times this coupon can be used"
    )
    valid_from: datetime | None = Field(
        None, description="ISO 8601 start of validity period"
    )
    valid_until: datetime | None = Field(
        None, description="ISO 8601 end of validity period"
    )
    applicable_product_ids: list[str] | None = Field(
        None, description="List of product UUIDs this coupon applies to (null = all)"
    )
    applicable_category_ids: list[str] | None = Field(
        None, description="List of category UUIDs this coupon applies to (null = all)"
    )
    # Phase 8.4 — type-specific config for BUY_X_GET_Y + TIERED. Null
    # for simple types.
    #   buy_x_get_y: {buy_quantity, get_quantity, get_discount_percentage,
    #                 buy_product_ids?, get_product_ids?}
    #   tiered:      {tiers: [{min_subtotal_cents, discount_percentage}, ...]}
    config: dict | None = Field(
        None,
        description=(
            "Type-specific configuration. Required for buy_x_get_y "
            "and tiered; ignored for simple types."
        ),
    )


class UpdateCouponRequest(BaseModel):
    """Update coupon request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "value": "30.00",
                "is_active": False,
            }
        }
    )

    code: str | None = Field(
        None, min_length=1, max_length=50, description="Coupon code"
    )
    coupon_type: str | None = Field(
        None, description="Discount type: percentage, fixed, or free_shipping"
    )
    value: Decimal | None = Field(None, ge=0, description="Discount value")
    min_order_amount: Decimal | None = Field(
        None, ge=0, description="Minimum order subtotal"
    )
    max_discount_amount: Decimal | None = Field(
        None, ge=0, description="Maximum discount cap"
    )
    usage_limit: int | None = Field(None, ge=1, description="Total usage limit")
    valid_from: datetime | None = Field(None, description="Validity start")
    valid_until: datetime | None = Field(None, description="Validity end")
    is_active: bool | None = Field(None, description="Enable or disable the coupon")
    applicable_product_ids: list[str] | None = Field(
        None, description="Product UUIDs this coupon applies to (null = all)"
    )
    applicable_category_ids: list[str] | None = Field(
        None, description="Category UUIDs this coupon applies to (null = all)"
    )
    # Phase 8.4 — replace type-specific config (Null = no change).
    config: dict | None = Field(
        None, description="Type-specific config for buy_x_get_y / tiered"
    )


class CouponResponse(BaseModel):
    """Coupon response schema."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "880e8400-e29b-41d4-a716-446655440000",
                "store_id": "660e8400-e29b-41d4-a716-446655440000",
                "code": "SUMMER25",
                "coupon_type": "percentage",
                "value": "25.00",
                "min_order_amount": "100.00",
                "max_discount_amount": "50.00",
                "usage_limit": 100,
                "usage_count": 12,
                "valid_from": "2025-06-01T00:00:00Z",
                "valid_until": "2025-08-31T23:59:59Z",
                "is_active": True,
                "is_expired": False,
                "is_usable": True,
                "created_at": "2025-05-15T10:00:00Z",
                "updated_at": "2025-05-15T10:00:00Z",
            }
        },
    )

    id: str = Field(description="Coupon UUID")
    store_id: str = Field(description="Owning store UUID")
    code: str = Field(description="Coupon code")
    coupon_type: str = Field(description="percentage, fixed, or free_shipping")
    value: str = Field(description="Formatted discount value")
    min_order_amount: str | None = Field(description="Minimum order amount")
    max_discount_amount: str | None = Field(description="Max discount cap")
    usage_limit: int | None = Field(description="Total usage limit")
    usage_count: int = Field(description="Number of times used")
    valid_from: str | None = Field(description="Validity start ISO 8601")
    valid_until: str | None = Field(description="Validity end ISO 8601")
    is_active: bool = Field(description="Whether the coupon is active")
    is_expired: bool = Field(description="Whether the coupon has expired")
    is_usable: bool = Field(description="Whether the coupon can be used right now")
    applicable_product_ids: list[str] | None = Field(
        None, description="Product UUIDs this coupon applies to"
    )
    applicable_category_ids: list[str] | None = Field(
        None, description="Category UUIDs this coupon applies to"
    )
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")

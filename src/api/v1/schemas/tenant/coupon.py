"""Coupon Pydantic schemas for store management."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class CreateCouponRequest(BaseModel):
    """Create coupon request schema."""

    code: str = Field(..., min_length=1, max_length=50)
    coupon_type: str = Field(..., description="percentage, fixed, or free_shipping")
    value: Decimal = Field(default=Decimal("0"), ge=0, description="Percentage (0-100) or fixed amount")
    min_order_amount: Decimal | None = Field(None, ge=0, description="Minimum order subtotal")
    max_discount_amount: Decimal | None = Field(None, ge=0, description="Max discount cap (for percentage)")
    usage_limit: int | None = Field(None, ge=1, description="Total usage limit")
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class UpdateCouponRequest(BaseModel):
    """Update coupon request schema."""

    code: str | None = Field(None, min_length=1, max_length=50)
    coupon_type: str | None = None
    value: Decimal | None = Field(None, ge=0)
    min_order_amount: Decimal | None = Field(None, ge=0)
    max_discount_amount: Decimal | None = Field(None, ge=0)
    usage_limit: int | None = Field(None, ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_active: bool | None = None


class CouponResponse(BaseModel):
    """Coupon response schema."""

    id: str
    store_id: str
    code: str
    coupon_type: str
    value: str
    min_order_amount: str | None
    max_discount_amount: str | None
    usage_limit: int | None
    usage_count: int
    valid_from: str | None
    valid_until: str | None
    is_active: bool
    is_expired: bool
    is_usable: bool
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

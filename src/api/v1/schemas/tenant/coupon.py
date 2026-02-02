"""Coupon Pydantic schemas for store management."""

from datetime import datetime

from pydantic import BaseModel, Field


class CreateCouponRequest(BaseModel):
    """Create coupon request schema."""

    code: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(None, max_length=500)
    discount_type: str = Field(..., description="percentage or fixed_amount")
    discount_value: int = Field(..., gt=0, description="Percentage (1-100) or cents for fixed")
    min_order_amount: int = Field(default=0, ge=0, description="Minimum order subtotal in cents")
    max_discount_amount: int | None = Field(None, ge=0, description="Max discount cap in cents (for percentage)")
    max_uses: int | None = Field(None, ge=1, description="Total usage limit")
    max_uses_per_customer: int | None = Field(None, ge=1, description="Per-customer usage limit")
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_active: bool = True


class UpdateCouponRequest(BaseModel):
    """Update coupon request schema."""

    description: str | None = Field(None, max_length=500)
    discount_value: int | None = Field(None, gt=0)
    min_order_amount: int | None = Field(None, ge=0)
    max_discount_amount: int | None = Field(None, ge=0)
    max_uses: int | None = Field(None, ge=1)
    max_uses_per_customer: int | None = Field(None, ge=1)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_active: bool | None = None


class CouponResponse(BaseModel):
    """Coupon response schema."""

    id: str
    store_id: str
    code: str
    description: str | None
    discount_type: str
    discount_value: int
    min_order_amount: int
    max_discount_amount: int | None
    max_uses: int | None
    max_uses_per_customer: int | None
    current_usage_count: int
    valid_from: str | None
    valid_to: str | None
    is_active: bool
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

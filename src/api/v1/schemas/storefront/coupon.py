"""Storefront coupon schemas."""

from decimal import Decimal

from pydantic import BaseModel, Field


class ApplyCouponRequest(BaseModel):
    """Request to validate and apply a coupon code."""

    coupon_code: str = Field(..., min_length=1, max_length=50)
    order_amount: Decimal = Field(..., ge=0, description="Cart subtotal")


class ApplyCouponResponse(BaseModel):
    """Response with calculated coupon discount."""

    coupon_id: str
    code: str
    coupon_type: str
    discount_amount: str
    free_shipping: bool

    class Config:
        from_attributes = True

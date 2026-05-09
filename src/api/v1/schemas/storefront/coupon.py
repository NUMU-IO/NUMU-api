"""Storefront coupon schemas."""

from decimal import Decimal

from pydantic import BaseModel, Field


class ApplyCouponRequest(BaseModel):
    """Request to validate and apply a coupon code."""

    coupon_code: str = Field(..., min_length=1, max_length=50)
    order_amount: Decimal = Field(..., ge=0, description="Cart subtotal")


class ApplyCouponResponse(BaseModel):
    """Response with calculated coupon discount.

    `promotion_id` is set when the coupon is wrapped in an active
    promotion (offers-v2). Storefronts can use it to record a `convert`
    event after the order completes; older clients ignore the field.
    """

    coupon_id: str
    code: str
    coupon_type: str
    discount_amount: str
    free_shipping: bool
    promotion_id: str | None = None

    class Config:
        from_attributes = True

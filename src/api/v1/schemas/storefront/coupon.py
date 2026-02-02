"""Storefront coupon schemas."""

from pydantic import BaseModel, Field


class ApplyCouponRequest(BaseModel):
    """Request to validate and apply a coupon code."""

    coupon_code: str = Field(..., min_length=1, max_length=50)
    subtotal: int = Field(..., ge=0, description="Cart subtotal in cents")


class ApplyCouponResponse(BaseModel):
    """Response with calculated coupon discount."""

    coupon_code: str
    discount_type: str
    discount_value: int
    calculated_discount: int = Field(description="Actual discount amount in cents")
    message: str

    class Config:
        from_attributes = True

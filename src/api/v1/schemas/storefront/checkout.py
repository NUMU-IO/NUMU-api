"""Checkout API schemas for storefront."""

from pydantic import BaseModel, Field


class CheckoutAddressRequest(BaseModel):
    """Shipping/billing address for checkout."""

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    address_line1: str = Field(..., min_length=1, max_length=255)
    address_line2: str | None = Field(None, max_length=255)
    city: str = Field(..., min_length=1, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: str = Field(..., min_length=2, max_length=100)
    phone: str | None = Field(None, max_length=20)


class CheckoutRequest(BaseModel):
    """Request to convert cart to order."""

    shipping_address: CheckoutAddressRequest
    billing_address: CheckoutAddressRequest | None = None
    shipping_cost: int = Field(default=0, ge=0, description="Shipping cost in cents")
    tax_amount: int = Field(default=0, ge=0, description="Tax amount in cents")
    discount_amount: int = Field(default=0, ge=0, description="Discount in cents")
    currency: str = Field(default="EGP", max_length=3)
    payment_method: str | None = None
    shipping_method: str | None = None
    customer_notes: str | None = Field(None, max_length=1000)

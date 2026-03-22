"""Checkout Pydantic schemas for storefront."""

from uuid import UUID

from pydantic import BaseModel, Field

from src.api.dependencies.sanitization import SanitizedStr
from src.api.v1.schemas.tenant.order import OrderAddressRequest


class CheckoutLineItem(BaseModel):
    """Line item submitted during checkout."""

    product_id: UUID
    variant_id: UUID | None = None
    quantity: int = Field(default=1, ge=1, le=999)


class CheckoutRequest(BaseModel):
    """Checkout request schema.

    The customer submits their cart items, shipping address,
    and payment preferences to create an order.
    """

    line_items: list[CheckoutLineItem] = Field(..., min_length=1)
    shipping_address: OrderAddressRequest
    billing_address: OrderAddressRequest | None = None
    payment_method: str | None = Field(None, description="e.g. paymob_card, cod")
    shipping_method: str | None = None
    customer_notes: SanitizedStr | None = Field(None, max_length=1000)
    coupon_code: str | None = Field(None, max_length=50)


class CheckoutResponse(BaseModel):
    """Checkout response schema.

    Returns the created order along with an optional payment URL
    if the chosen method requires redirect-based payment.
    """

    order_id: str
    order_number: str
    total: int = Field(description="Total in cents")
    currency: str
    payment_status: str
    payment_url: str | None = Field(
        None,
        description="Redirect URL for payment gateway (null for COD)",
    )
    payment_data: dict | None = Field(
        None,
        description="Provider-specific payment data for client-side rendering (e.g., Kashier hash)",
    )

    class Config:
        from_attributes = True

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
    # Guest checkout fields (used when not authenticated)
    guest_email: str | None = Field(
        None, max_length=254, description="Email for guest checkout"
    )
    shipping_method: str | None = None
    customer_notes: SanitizedStr | None = Field(None, max_length=1000)
    coupon_code: str | None = Field(None, max_length=50)
    # UTM attribution (captured from URL on storefront)
    utm_source: str | None = Field(None, max_length=200)
    utm_medium: str | None = Field(None, max_length=200)
    utm_campaign: str | None = Field(None, max_length=200)
    # Session fingerprint for funnel deduplication. Same value the storefront
    # sends to /track for page_view/product_view/add_to_cart, so the
    # COUNT(DISTINCT session_fingerprint) funnel query can connect this
    # checkout to the visitor's earlier steps.
    session_fingerprint: str | None = Field(None, max_length=64)
    # Merchant-defined custom checkout fields. Keys are the custom field IDs
    # from store.settings.checkout_fields; values are the raw user inputs.
    # Validated server-side against the live config — required-field misses
    # and bad option values 400 before an order is created.
    custom_fields: dict[str, object] | None = Field(
        None, description="Map of custom field id → submitted value"
    )


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
    paymob_client_secret: str | None = Field(
        None,
        description="Paymob client secret for Pixel Embedded checkout",
    )
    paymob_public_key: str | None = Field(
        None,
        description="Paymob public key for Pixel Embedded checkout",
    )

    class Config:
        from_attributes = True

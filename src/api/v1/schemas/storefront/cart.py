"""Cart Pydantic schemas for storefront."""

from uuid import UUID

from pydantic import BaseModel, Field


class AddCartItemRequest(BaseModel):
    """Add item to cart request schema."""

    product_id: UUID
    variant_id: UUID | None = None
    quantity: int = Field(default=1, ge=1, le=999)


class UpdateCartItemRequest(BaseModel):
    """Update cart item quantity request schema."""

    quantity: int = Field(..., ge=1, le=999)


class CartItemResponse(BaseModel):
    """Cart item response schema."""

    id: str
    product_id: str
    product_name: str
    variant_id: str | None = None
    variant_name: str | None = None
    sku: str | None = None
    quantity: int
    unit_price: int = Field(
        description=(
            "Snapshotted price in cents (captured when the line was added). "
            "Use this for the cart subtotal — it is NOT the live product price."
        ),
    )
    total_price: int = Field(description="quantity * unit_price in cents")
    current_price: int | None = Field(
        default=None,
        description=(
            "Live product price in cents at the time of the cart fetch. "
            "When this differs from `unit_price`, the merchant changed the "
            "price after the line was added — themes can surface a "
            "'price changed since you added it' notice."
        ),
    )
    price_changed: bool = Field(
        default=False,
        description="True iff `current_price` differs from the snapshotted `unit_price`.",
    )
    image_url: str | None = None
    in_stock: bool = True
    available_now: int | None = Field(
        default=None,
        description=(
            "Live remaining inventory at the time of the cart fetch. "
            "When less than `quantity`, the line is partially fulfillable "
            "and themes should surface a 'reduce quantity' nudge."
        ),
    )
    sold_out_now: bool = Field(
        default=False,
        description=(
            "True iff the product flipped to out-of-stock between the time "
            "the line was added and now. The Checkout button should be "
            "disabled when any cart line is sold_out_now=true."
        ),
    )

    class Config:
        from_attributes = True


class CartResponse(BaseModel):
    """Full cart response schema."""

    items: list[CartItemResponse]
    item_count: int = Field(description="Total number of distinct line items")
    total_quantity: int = Field(description="Sum of all item quantities")
    subtotal: int = Field(description="Subtotal in cents")
    currency: str = "EGP"

    class Config:
        from_attributes = True

"""Cart Pydantic schemas for storefront."""

from uuid import UUID

from pydantic import BaseModel, Field


class AddCartItemRequest(BaseModel):
    """Add item to cart request schema."""

    product_id: UUID
    variant_id: UUID | None = None
    quantity: int = Field(default=1, ge=1, le=999)
    # Picker axes ({"Color": "Black", "Size": "L"}) — variant_name fallback for
    # products without resolvable variant rows (legacy attributes-JSON shape).
    selected_options: dict[str, str] | None = None


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
    category_id: str | None = Field(
        default=None,
        description=(
            "The product's category. Exposed so clients can echo it back to "
            "`POST /cart/discounts` — category-scoped promotions match on it, "
            "and a preview that omits it under-reports the discount the order "
            "will actually get."
        ),
    )
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


class AppliedPromotionResponse(BaseModel):
    """One automatic promotion that priced this cart.

    Deliberately the SAME shape as the order's persisted
    ``applied_promotions`` snapshot, so the cart, the checkout summary and
    the order record all read identically and a theme can render one
    component for all three.
    """

    id: str
    title: str
    title_ar: str | None = None
    amount: int = Field(
        description="This promotion's own contribution, in integer cents"
    )


class CartResponse(BaseModel):
    """Full cart response schema.

    Discount fields (added for offers-v2 cart visibility): without them a
    shopper whose cart qualified for an automatic promotion saw the full
    price here while checkout charged the discounted total — the offer
    fired invisibly and so could never motivate the extra unit that
    unlocked it. All amounts are integer cents, like every other money
    field on this response.
    """

    items: list[CartItemResponse]
    item_count: int = Field(description="Total number of distinct line items")
    total_quantity: int = Field(description="Sum of all item quantities")
    subtotal: int = Field(description="Subtotal in cents")
    currency: str = "EGP"
    automatic_discount_cents: int = Field(
        default=0,
        description=(
            "Sum of automatic (no-code) promotions applied to this cart, in "
            "cents. Computed by the same engine the checkout charges with."
        ),
    )
    discount_amount: int = Field(
        default=0,
        description=(
            "Total discount applied to this cart in cents — automatic "
            "promotions plus any promotion-backed code pinned on the cart."
        ),
    )
    total: int = Field(
        default=0,
        description="Post-discount cart total in cents (subtotal - discount_amount).",
    )
    applied_promotions: list[AppliedPromotionResponse] = Field(
        default_factory=list,
        description="Named breakdown of the automatic promotions that fired.",
    )

    class Config:
        from_attributes = True

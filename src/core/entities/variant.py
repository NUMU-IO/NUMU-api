"""Product variant entity — Phase 8.1.

A `Variant` is one specific purchasable combination of a product's
option axes. For a T-shirt with options Size × Color, each Variant
represents one (Size, Color) pair: ("S", "Red"), ("M", "Red"),
("S", "Blue"), etc.

Variant is the unit of price + inventory tracking. The product itself
keeps a `price` for "starting from" display, but the **checkout
charges variant.price** and **stock decrements variant.inventory_quantity**.

Before Phase 8.1, single-SKU products had no variant rows — checkout
read product.price directly and decremented product.quantity. The
Phase 8.1 migration backfills a "default variant" for every existing
product so the variant table becomes the canonical source for
price/inventory going forward; product.price stays as a denormalized
display field updated whenever the cheapest variant changes.

Option axes (size/color/material/etc) live on `Product.options`
(JSONB list) as `[{name, position, values: [...]}]`. They're stored
on the Product because options apply to *all* of a product's
variants — a T-shirt's option axes are "Size" + "Color", regardless
of which specific S/M/L × Red/Blue/Green you're looking at.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from src.core.entities.base import BaseEntity
from src.core.value_objects.money import Money


class Variant(BaseEntity):
    """One purchasable combination of a product's option axes."""

    tenant_id: UUID | None = None
    store_id: UUID
    product_id: UUID
    # Stable per-product position for display ordering (e.g. the
    # variant matrix in the hub PDP). Independent of any axis values.
    position: int = Field(default=0, ge=0)
    # Map from axis name → chosen value. For a product with
    # `options=[{name:"Size",values:["S","M","L"]}, {name:"Color",...}]`,
    # a valid `option_values` is `{"Size": "M", "Color": "Red"}`.
    # Keys must be a subset of `product.options[*].name`; values must
    # be elements of the matching axis's `values` list. Validation is
    # at the service layer (the entity itself just stores the dict).
    option_values: dict[str, str] = Field(default_factory=dict)
    # Money fields — variant prices override the product's. Cart line
    # items snapshot from `variant.price` at add-time, not the product.
    price: Money
    compare_at_price: Money | None = None
    cost_price: Money | None = None
    # SKU + barcode are per-variant (a T-shirt's S/M/L variants get
    # different SKUs); both are optional because not every merchant
    # tracks barcodes.
    sku: str | None = None
    barcode: str | None = None
    # Inventory tracking — per-variant. Multi-location inventory
    # (Phase 8.2) will replace this single column with a join to
    # `inventory_levels` aggregating across locations; until then this
    # is the canonical stock count.
    inventory_quantity: int = Field(default=0, ge=0)
    # Variants can have their own image (e.g. a "Red" variant shows
    # the red photo on the PDP when selected). Empty → fall back to
    # the product's first image.
    image_url: str | None = None
    # Optional per-variant weight override (for shipping zones that
    # rate-quote by weight).
    weight: float | None = None
    # Free-form per-variant metadata — used by integrations (e.g.
    # ETA invoice tax code) that need a variant-specific value
    # without warranting a column.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("compare_at_price", "cost_price", mode="before")
    @classmethod
    def _coerce_money(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, Money):
            return v
        if isinstance(v, dict):
            return Money.model_validate(v)
        return v

    @property
    def is_in_stock(self) -> bool:
        return self.inventory_quantity > 0

    @property
    def is_on_sale(self) -> bool:
        if self.compare_at_price is None:
            return False
        return self.price < self.compare_at_price


class ProductOption(BaseEntity):
    """One axis on a product.

    Stored as JSONB on `Product.options` rather than its own table:
    options are tightly coupled to their product (no cross-product
    reuse), the cardinality is tiny (Shopify caps at 3 axes), and
    embedding avoids a join on every PDP read.

    The hub PDP editor exposes these as a "Sizes: S, M, L" sub-form.
    Adding/removing values cascades: removing "L" from a Size option
    archives any variant that uses Size=L (we never delete variants
    in case they're referenced by historical orders).
    """

    name: str
    position: int = Field(default=0, ge=0)
    values: list[str] = Field(default_factory=list)

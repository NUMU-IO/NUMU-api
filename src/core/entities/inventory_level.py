"""Per-location inventory level — Phase 8.2.

`InventoryLevel` is the (variant × location) join that tracks how
much of a specific variant lives at a specific location. The sum of
levels across locations for a given variant equals
`Variant.inventory_quantity` — kept in sync by the application layer
so the hot-path cart/checkout reads stay on the variant's single
column without a join.

Order fulfillment routing (which location ships a given order) lives
in the ShippingResolver / fulfillment service layer. For Phase 8.2
the level is purely a *display + allocation* surface: the hub shows
"Cairo HQ: 12, Alex Warehouse: 4" and the merchant can transfer
stock between rows. Picking which location actually fulfills an
order lands later in the order-routing rollout.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class InventoryLevel(BaseEntity):
    tenant_id: UUID
    store_id: UUID
    variant_id: UUID
    location_id: UUID
    # Available count at this specific location. NOT negative — the
    # decrement path raises rather than going below zero.
    available: int = Field(default=0, ge=0)
    # On-hand minus reserved. We expose `available` as the
    # subtractable count; `reserved` tracks stock allocated to
    # in-progress orders (between checkout-validate and
    # payment-confirm). v1 keeps reserved at 0 because we deduct
    # straight from `available` at checkout-create — but the column
    # exists so we can move to a reserve-then-confirm flow later
    # without a schema change.
    reserved: int = Field(default=0, ge=0)

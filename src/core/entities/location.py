"""Store location entity — Phase 7.2.

A `Location` is a physical or logical fulfillment site a merchant can
operate from. Cairo HQ. Alex warehouse. The "online" store. A pop-up.
The Location.address is the origin point for `ShippingResolver` rate
calculations; `fulfills_orders` controls whether the location can be
selected as a fulfillment source for online orders; `fulfills_pickup`
controls whether storefront checkout offers it as an "I'll pick up
in-store" option.

Multi-location inventory (`InventoryLevel` join) lands in Phase 8.2.
Today every location ships orders from the same shared product
quantity — locations are purely a *pickup-availability* signal in
v7.2.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from src.core.entities.address import Address
from src.core.entities.base import BaseEntity


class Location(BaseEntity):
    tenant_id: UUID
    store_id: UUID
    # Display name for the location: "Cairo HQ", "Alex Warehouse".
    name: str
    # Optional Arabic name — surfaced when the storefront locale is Arabic.
    name_ar: str | None = None
    # Physical address used as the rate-calculation origin for any
    # orders fulfilled from this location.
    address: Address
    # When true the location can be selected as a fulfillment source
    # for online orders. Some locations exist for inventory tracking
    # only and shouldn't show up in fulfillment pickers (e.g. a
    # returns-receiving warehouse).
    fulfills_orders: bool = True
    # When true the location appears in the storefront's checkout
    # shipping step as a pickup option ("Pick up at <name>"). Pickup
    # bypasses shipping rate calculation — amount_cents is 0 and the
    # ship_to address is set to the location's address on the order.
    fulfills_pickup: bool = False
    # Free-text instructions surfaced after pickup is selected
    # ("Park on Sherif St., enter via side door, ask for Ahmed").
    pickup_instructions: str | None = None
    pickup_instructions_ar: str | None = None
    # Disabled locations are hidden from all pickers + checkout but
    # are preserved for historical-order traceability (old orders
    # might still reference them).
    is_active: bool = True
    # Display ordering in pickers. Lower sorts first.
    position: int = Field(default=0)

"""Wishlist domain entity (Phase 4.5).

A wishlist is a per-customer (or per-session, for guests) bag of
saved products. Mirrors the cart's owner pattern — same
`(customer_id OR session_id)` discriminator — so guest wishlists
merge into the customer's authenticated wishlist on login the same
way guest carts do.

The SDK already ships `useWishlist(storeId)` with localStorage
fallback. This backend gives an authed customer cross-device sync.
The hook will check `useCustomer()` and call the server when authed,
falling back to localStorage only when anonymous.
"""

from datetime import datetime
from uuid import UUID

from src.core.entities.base import BaseEntity


class WishlistItem(BaseEntity):
    """Single product (+ optional variant) saved to a wishlist."""

    store_id: UUID
    tenant_id: UUID
    customer_id: UUID | None = None
    # Guest wishlists are keyed by the same `numu_cart_session` cookie
    # the cart uses, so guest cart + guest wishlist merge in lockstep
    # on login.
    session_id: str | None = None
    product_id: UUID
    variant_id: UUID | None = None
    added_at: datetime

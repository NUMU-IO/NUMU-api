"""AbandonedCheckout entity — persisted checkout state distinct from a confirmed Order.

The Redis-backed `Cart` (in `src/core/entities/cart.py`) holds the live shopping
cart, keyed by session. Once the customer hits the checkout form, the
storefront persists an `AbandonedCheckout` row in Postgres. Successful
payment graduates the row into an Order and sets `recovered_at`. The
background job marks rows abandoned once they sit inactive past the
abandonment threshold (default 1 hour).

Keeping abandoned checkouts in a separate table from orders (Shopify model)
lets order analytics stay clean of half-finished sessions and gives us a
discrete place to run recovery / RFM campaigns.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from src.core.entities.base import BaseEntity


class AbandonedCheckout(BaseEntity):
    """Persisted in-progress or abandoned checkout."""

    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
        use_enum_values=False,
        populate_by_name=True,
    )

    store_id: UUID
    tenant_id: UUID | None = None

    # Nullable for guest checkouts. Filled once the storefront identifies
    # the shopper (email match or login).
    customer_id: UUID | None = None

    # Snapshot of cart contents at last update. Same shape as OrderLineItem.
    line_items: list[dict[str, Any]] = Field(default_factory=list)

    # Contact info captured during checkout. Drives recovery emails.
    email: str | None = None
    phone: str | None = None

    # Shipping address sketch — partial, since checkout could abandon at
    # any step. Stored as a flat dict matching OrderAddress shape.
    shipping_address: dict[str, Any] | None = None

    # Totals snapshot (cents). Recomputed by the storefront on each update.
    subtotal: int = 0
    shipping_cost: int = 0
    tax_amount: int = 0
    discount_amount: int = 0
    total: int = 0
    currency: str = "EGP"

    # Discounts / attribution
    coupon_code: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None

    # Lifecycle timestamps. `last_activity_at` ticks on every storefront
    # PATCH; the background job flips `abandoned_at` once `now - last_activity_at`
    # crosses the abandonment threshold.
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    abandoned_at: datetime | None = None
    recovered_at: datetime | None = None
    recovery_email_sent_at: datetime | None = None
    # Linkage to the converted order, if this checkout graduated.
    recovered_order_id: UUID | None = None

    # Free-form bag (session fingerprint, device, IP) for analytics.
    extra_data: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_abandoned(self) -> bool:
        return self.abandoned_at is not None and self.recovered_at is None

    @property
    def is_recovered(self) -> bool:
        return self.recovered_at is not None

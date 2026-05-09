"""Back-in-stock product subscription entity.

Customers ask to be notified when an out-of-stock product comes back.
Phase 3.5 — single subscription per (product, variant?, email). The
notify_back_in_stock storefront endpoint upserts; the Celery sweep
task delivers the email and stamps `notified_at`.

Why a separate table instead of a flag on `customer_addresses`:
    1. Anonymous visitors can subscribe without an account.
    2. Multiple subscriptions per customer to different products
       across stores need independent state.
    3. Sweep job needs to scan only un-notified rows; storing on the
       customer would require per-customer JSONB scanning.
"""

from datetime import datetime
from uuid import UUID

from src.core.entities.base import BaseEntity


class ProductSubscription(BaseEntity):
    """One pending notification request."""

    store_id: UUID
    tenant_id: UUID
    product_id: UUID
    # Variant-scoped subscriptions — when a customer wants the
    # "Large / Blue" SKU specifically. Null subscribes to "any variant
    # in stock" which matches the product-level `is_in_stock` flag.
    variant_id: UUID | None = None
    # Customers don't have to be logged in to subscribe — email is the
    # canonical identifier. We accept arbitrary case but normalize to
    # lowercase before insert (handled at the repository layer).
    email: str
    # Set when the back-in-stock email is delivered. Acts as both the
    # sweep-skip flag (notified_at IS NOT NULL → skip) and the audit
    # log entry. Subsequent stockouts re-add a fresh row rather than
    # clearing this — merchants can see notification history.
    notified_at: datetime | None = None

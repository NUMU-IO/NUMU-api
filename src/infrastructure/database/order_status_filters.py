"""The single definition of "does this order count as revenue".

Before this module the platform carried **three** different answers, and
which one a merchant saw depended on which screen they opened and whether
the nightly rollup happened to have run:

1. ``analytics_repository._NON_REVENUE_STATUSES_LC`` excluded cancelled,
   refunded, draft and payment_failed  (live analytics breakdowns)
2. ``order_repository`` excluded only CANCELLED and REFUNDED
   (``get_revenue_by_date_range``, ``get_daily_aggregates`` — dashboard +
   the analytics live fallback)
3. the rollup task excluded only payment_failed and draft — i.e. it
   **counted cancelled and refunded orders as revenue**

So ``/overview`` served from the rollup disagreed with ``/overview``
served from its own live fallback, and both disagreed with
``/sales-by-location``. Worse, inside a single rollup row
``total_revenue_cents`` (definition 3) could never be reconciled against
``revenue_by_location_json`` (definition 1) — the breakdowns did not sum
to the total by construction.

Every revenue aggregate must now import from here.

Enum-label caveat (do not "simplify" this to a plain enum comparison):
    The Postgres ``orderstatus`` enum carries a MIXED-case set of labels —
    UPPERCASE member names for most values, but lowercase for
    ``payment_failed`` / ``pending_deposit`` / ``returned``. Binding
    ``OrderStatus.PAYMENT_FAILED`` raises ``invalid input value for enum``
    because no uppercase label exists, and a single-case text comparison
    silently misses rows stored in the other case. ``lower(status::text)``
    is the only spelling that matches every row.

RETURNED deliberately stays IN booked revenue: it was real demand that
converted. The collected-revenue and COD views subtract it separately.
"""

from __future__ import annotations

from sqlalchemy import String, cast, func

# Statuses that never represent demand:
#   cancelled      — killed by merchant or customer
#   refunded       — money went back
#   draft          — merchant-only, never visible to a customer, never billable
#   payment_failed — customer never completed payment
NON_REVENUE_STATUSES_LC: tuple[str, ...] = (
    "cancelled",
    "refunded",
    "draft",
    "payment_failed",
)


def status_lc(col):
    """``lower(status::text)`` — case-proof ``orderstatus`` comparison."""
    return func.lower(cast(col, String))


def exclude_non_revenue(col):
    """WHERE-clause fragment keeping only orders that count as revenue.

    Usage::

        from src.infrastructure.database.models.tenant.order import OrderModel
        query.where(exclude_non_revenue(OrderModel.status))
    """
    return status_lc(col).notin_(NON_REVENUE_STATUSES_LC)

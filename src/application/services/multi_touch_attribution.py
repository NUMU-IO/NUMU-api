"""Multi-touch attribution orchestration — orders × touches × model.

Sits between the analytics route and the pure-function calculators in
``attribution_models``:

1. Query non-cancelled orders for ``store_id`` in the date window
   that have a known ``customer_id``. Anonymous orders (guest
   checkout without a session_fingerprint that matches a known
   customer) can't be multi-touch-attributed because we don't have a
   touch history to distribute credit over.
2. Fetch each customer's ``customer_touches`` up to and including
   the order's ``created_at`` (capped at ``MAX_TOUCHES_PER_ORDER``).
3. Run the selected model to distribute the order's revenue.
4. Aggregate per-channel credit across all orders.

Performance caps (these are large enough that a real merchant never
hits them; they exist to bound the worst case):

* ``MAX_ORDERS_PER_RUN = 5000`` — past this, fall back to telling the
  caller to narrow the window. Per-order touch fetch is the
  expensive part.
* ``MAX_TOUCHES_PER_ORDER = 50`` — bot accounts or buggy storefronts
  can accumulate hundreds of touches; cap at 50 so the credit
  calculation stays bounded.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.attribution_models import (
    AttributionModel,
    Touch,
    attribute,
)
from src.core.entities.order import OrderStatus
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.customer_touch import (
    CustomerTouchModel,
)
from src.infrastructure.database.models.tenant.marketing_campaign import (
    MarketingCampaignModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel

MAX_ORDERS_PER_RUN = 5_000
MAX_TOUCHES_PER_ORDER = 50

_NON_REVENUE_STATUSES = (OrderStatus.CANCELLED, OrderStatus.REFUNDED)


class AttributionWindowTooLargeError(RuntimeError):
    """Raised when the orchestrator hits the order cap.

    The route layer maps this to a 400 with a "narrow your date
    range" message. We surface it as a typed exception instead of
    silently truncating so the merchant doesn't get a misleadingly
    low number.
    """


def _channel_label(touch: Touch) -> str:
    """Normalized channel identifier for cross-touch aggregation.

    Pattern matches :py:meth:`AnalyticsRepository.traffic_sources`:
    lowercased + trimmed ``utm_source``, fallback to ``"direct"`` so
    organic / direct touches land in a recognizable bucket.
    """
    return (touch.utm_source or "").strip().lower() or "direct"


def _row_to_touch(row: CustomerTouchModel) -> Touch:
    """SQLAlchemy row -> immutable Touch value object."""
    return Touch(
        id=row.id,
        ts=row.ts,
        utm_source=row.utm_source,
        utm_medium=row.utm_medium,
        utm_campaign=row.utm_campaign,
        campaign_id=row.campaign_id,
    )


async def _fetch_orders(
    session: AsyncSession,
    store_id: UUID,
    date_from: datetime,
    date_to: datetime,
) -> list[OrderModel]:
    """Pull non-cancelled orders in window with a known customer.

    Sort: created_at ASC. The route layer assumes deterministic
    ordering so a paginated UI gets a stable snapshot.
    """
    query = (
        select(OrderModel)
        .where(
            OrderModel.store_id == store_id,
            OrderModel.created_at >= date_from,
            OrderModel.created_at <= date_to,
            OrderModel.status.notin_(_NON_REVENUE_STATUSES),
            OrderModel.customer_id.isnot(None),
        )
        .order_by(OrderModel.created_at.asc())
        .limit(MAX_ORDERS_PER_RUN + 1)
    )
    tid = get_tenant_id()
    if tid:
        query = query.where(OrderModel.tenant_id == tid)
    result = await session.execute(query)
    return list(result.scalars().all())


async def _fetch_touches_for_customers(
    session: AsyncSession,
    store_id: UUID,
    customer_ids: Iterable[UUID],
) -> dict[UUID, list[CustomerTouchModel]]:
    """Fetch every touch for the given customers, sorted by ts ASC.

    One query for the full set; we partition in Python rather than
    issuing N queries. The (customer_id, ts) partial index covers
    this scan.

    SEC: tenant_id filter applied alongside store_id. Defense in
    depth — store_id already binds to one tenant, but every other
    read path on customer_touches also enforces tenant_id explicitly
    (see customer_touch_service + analytics_repository patterns). A
    future bug elsewhere — e.g. a store_id resolution that briefly
    crosses tenants — must not turn this hot orchestration path into
    a cross-tenant leak.
    """
    customer_id_list = [cid for cid in customer_ids if cid is not None]
    if not customer_id_list:
        return {}
    query = (
        select(CustomerTouchModel)
        .where(
            CustomerTouchModel.store_id == store_id,
            CustomerTouchModel.customer_id.in_(customer_id_list),
        )
        .order_by(CustomerTouchModel.ts.asc())
    )
    tid = get_tenant_id()
    if tid:
        query = query.where(CustomerTouchModel.tenant_id == tid)
    result = await session.execute(query)
    out: dict[UUID, list[CustomerTouchModel]] = {}
    for row in result.scalars().all():
        out.setdefault(row.customer_id, []).append(row)
    return out


def _touches_at_or_before(
    touches: list[CustomerTouchModel],
    cutoff: datetime,
    *,
    limit: int,
) -> list[Touch]:
    """Filter a customer's touch list down to those at/before the cutoff.

    Operates on the already-sorted-ASC list from
    ``_fetch_touches_for_customers``. Returns the most-recent ``limit``
    touches when the count exceeds the cap (bots / buggy storefronts
    should not blow up the credit split).
    """
    filtered = [t for t in touches if t.ts <= cutoff]
    if len(filtered) > limit:
        filtered = filtered[-limit:]
    return [_row_to_touch(t) for t in filtered]


async def _fetch_campaign_names(
    session: AsyncSession,
    store_id: UUID,
    campaign_ids: set[UUID],
) -> dict[UUID, str]:
    """Bulk-load campaign names for the breakdown.

    The per-channel rollup is keyed on ``utm_source``, but the
    per-campaign rollup needs the human-readable campaign name (not
    just the UUID). One query for the full set used in the result.
    """
    if not campaign_ids:
        return {}
    query = select(MarketingCampaignModel.id, MarketingCampaignModel.name).where(
        MarketingCampaignModel.store_id == store_id,
        MarketingCampaignModel.id.in_(campaign_ids),
    )
    tid = get_tenant_id()
    if tid:
        query = query.where(MarketingCampaignModel.tenant_id == tid)
    result = await session.execute(query)
    return {row[0]: row[1] for row in result.all()}


async def compute_multi_touch_attribution(
    *,
    session: AsyncSession,
    store_id: UUID,
    date_from: datetime,
    date_to: datetime,
    model: AttributionModel,
) -> dict:
    """End-to-end orchestration. Returns the response shape the
    /analytics/multi-touch endpoint hands back to the hub.

    Output shape::

        {
          "model": "linear",
          "total_orders": int,
          "total_revenue_cents": int,
          "by_channel": [{"channel", "credit_cents", "credit_pct"}],
          "by_campaign": [
              {"campaign_id", "campaign_name", "credit_cents", "credit_pct"}
          ],
        }

    Raises ``AttributionWindowTooLargeError`` when the order count in
    window exceeds ``MAX_ORDERS_PER_RUN``.
    """
    orders = await _fetch_orders(session, store_id, date_from, date_to)
    if len(orders) > MAX_ORDERS_PER_RUN:
        raise AttributionWindowTooLargeError(
            f"window contains > {MAX_ORDERS_PER_RUN} orders — narrow the date "
            "range and try again"
        )

    customer_ids = {o.customer_id for o in orders if o.customer_id}
    touches_by_customer = await _fetch_touches_for_customers(
        session, store_id, customer_ids
    )

    channel_credit: dict[str, int] = {}
    campaign_credit: dict[UUID, int] = {}
    total_revenue = 0
    # Count only orders that actually contributed to the credit math.
    # Reporting ``len(orders)`` would over-count: orders with
    # ``total <= 0`` are skipped from the revenue + attribution loop
    # below (they have nothing to attribute), and including them in
    # ``total_orders`` would make the merchant see "X orders /
    # $Y revenue" with X > the number of orders behind Y. Keeps the
    # response internally consistent.
    attributed_orders = 0

    for order in orders:
        revenue = int(order.total or 0)
        if revenue <= 0:
            continue
        total_revenue += revenue
        attributed_orders += 1
        cust_touches = touches_by_customer.get(order.customer_id) or []
        touches = _touches_at_or_before(
            cust_touches,
            cutoff=order.created_at,
            limit=MAX_TOUCHES_PER_ORDER,
        )
        if not touches:
            # No touch history — credit to "direct" so the row isn't
            # silently dropped.
            channel_credit["direct"] = channel_credit.get("direct", 0) + revenue
            continue
        credits = attribute(
            model=model,
            touches=touches,
            revenue_cents=revenue,
            conversion_at=order.created_at,
        )
        for touch, credit in credits:
            if credit == 0:
                continue
            ch = _channel_label(touch)
            channel_credit[ch] = channel_credit.get(ch, 0) + credit
            if touch.campaign_id is not None:
                campaign_credit[touch.campaign_id] = (
                    campaign_credit.get(touch.campaign_id, 0) + credit
                )

    campaign_names = await _fetch_campaign_names(
        session, store_id, set(campaign_credit.keys())
    )

    def _pct(part: int, total: int) -> float:
        return round((part / total) * 100, 2) if total > 0 else 0.0

    by_channel = sorted(
        [
            {
                "channel": ch,
                "credit_cents": credit,
                "credit_pct": _pct(credit, total_revenue),
            }
            for ch, credit in channel_credit.items()
        ],
        key=lambda r: r["credit_cents"],
        reverse=True,
    )

    by_campaign = sorted(
        [
            {
                "campaign_id": str(cid),
                "campaign_name": campaign_names.get(cid, "(unknown)"),
                "credit_cents": credit,
                "credit_pct": _pct(credit, total_revenue),
            }
            for cid, credit in campaign_credit.items()
        ],
        key=lambda r: r["credit_cents"],
        reverse=True,
    )

    return {
        "model": model,
        "total_orders": attributed_orders,
        "total_revenue_cents": total_revenue,
        "by_channel": by_channel,
        "by_campaign": by_campaign,
    }


# Quiet the lint that `and_` is imported-but-unused — kept for
# future filter additions (e.g. campaign filter) without churn.
_ = and_

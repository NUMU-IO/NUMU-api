"""Analytics aggregation repository.

These queries do GROUP BY work in the database rather than materializing
order rows in Python. Several previous endpoints in
``api/v1/routes/stores/analytics.py`` paginated raw orders with a hard
``limit=N`` cap then bucketed them in Python; for any store that exceeded
the cap the dashboards silently lied. Each method here returns a
fully-aggregated result that does not depend on a row-count cap.

All methods enforce tenant scoping via ``_tenant_filter`` and exclude
cancelled/refunded orders unless the caller opts in. Monetary values stay
in **cents** (int) on the wire — callers convert to display currency at
the edge.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Integer,
    case,
    cast,
    extract,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.order import OrderStatus
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.funnel_event import (
    FunnelEventModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel

_NON_REVENUE_STATUSES = (OrderStatus.CANCELLED, OrderStatus.REFUNDED)


class AnalyticsRepository:
    """SQL-side aggregations powering the merchant analytics endpoints."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(OrderModel.tenant_id == tid)
        return query

    def _store_window(self, store_id: UUID, date_from: datetime, date_to: datetime):
        """Common WHERE clause: store + date range + non-cancelled."""
        return [
            OrderModel.store_id == store_id,
            OrderModel.created_at >= date_from,
            OrderModel.created_at <= date_to,
            OrderModel.status.notin_(_NON_REVENUE_STATUSES),
        ]

    # ── Traffic sources ─────────────────────────────────────────────
    async def traffic_sources(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """Group orders by normalized utm_source.

        ``LOWER(TRIM(utm_source))`` collapses ``Facebook`` / ``facebook``
        / ``  Facebook `` into a single bucket; ``NULL`` falls into a
        ``"direct"`` bucket so the dashboard can show direct traffic
        without a separate code path.
        """
        source_expr = func.coalesce(
            func.lower(func.trim(OrderModel.utm_source)), "direct"
        ).label("source")
        query = (
            select(
                source_expr,
                func.count(OrderModel.id).label("orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
            )
            .where(*self._store_window(store_id, date_from, date_to))
            .group_by(source_expr)
            .order_by(func.count(OrderModel.id).desc())
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [
            {
                "source": row.source,
                "orders": int(row.orders),
                "revenue_cents": int(row.revenue_cents),
            }
            for row in result.all()
        ]

    # ── Sales by location ───────────────────────────────────────────
    async def sales_by_location(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """Group orders by shipping_address.city (normalized).

        Uses ``LOWER(TRIM(shipping_address->>'city'))`` so ``Cairo`` and
        ``cairo`` and ``Cairo  `` collapse. Falls back to the ``state``
        field when ``city`` is missing — same fallback the previous
        Python loop did, but without the casing collisions.
        """
        city = func.lower(
            func.trim(
                func.coalesce(
                    OrderModel.shipping_address["city"].astext,
                    OrderModel.shipping_address["state"].astext,
                    "",
                )
            )
        )
        # NULLIF turns the empty-string fallback into NULL so we can
        # filter empties out cleanly without scanning twice.
        location_expr = func.nullif(city, "").label("location")
        query = (
            select(
                location_expr,
                func.count(OrderModel.id).label("orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
            )
            .where(*self._store_window(store_id, date_from, date_to))
            .where(location_expr.isnot(None))
            .group_by(location_expr)
            .order_by(func.sum(OrderModel.total).desc())
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [
            {
                "location": row.location,
                "orders": int(row.orders),
                "revenue_cents": int(row.revenue_cents),
            }
            for row in result.all()
        ]

    # ── Orders breakdown — status + payment_method + DOW + hour ─────
    async def orders_by_status(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, int]:
        """Order count grouped by status. Includes cancelled/refunded
        because this view is *about* status — the merchant wants to see
        them."""
        query = (
            select(OrderModel.status, func.count(OrderModel.id))
            .where(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= date_from,
                OrderModel.created_at <= date_to,
            )
            .group_by(OrderModel.status)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return {row[0].value: int(row[1]) for row in result.all()}

    async def orders_by_payment_method(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, dict]:
        """{payment_method: {count, revenue_cents}}."""
        query = (
            select(
                OrderModel.payment_method,
                func.count(OrderModel.id).label("count"),
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
            )
            .where(*self._store_window(store_id, date_from, date_to))
            .group_by(OrderModel.payment_method)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return {
            (row[0] or "unknown"): {
                "count": int(row.count),
                "revenue_cents": int(row.revenue_cents),
            }
            for row in result.all()
        }

    async def orders_by_day_of_week(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[int, dict]:
        """Histogram by Postgres DOW (0=Sunday … 6=Saturday).

        Returns ``{dow: {orders, revenue_cents}}``. The endpoint maps
        Postgres DOW into the desired display order (Mon–Sun).
        """
        dow = extract("dow", OrderModel.created_at).label("dow")
        query = (
            select(
                dow,
                func.count(OrderModel.id).label("orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
            )
            .where(*self._store_window(store_id, date_from, date_to))
            .group_by(dow)
            .order_by(dow)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return {
            int(row.dow): {
                "orders": int(row.orders),
                "revenue_cents": int(row.revenue_cents),
            }
            for row in result.all()
        }

    async def fulfillment_time_stats(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, float]:
        """avg / p50 / p95 hours from order ``created_at`` to ``fulfilled_at``.

        Uses Postgres ``percentile_cont`` rather than an in-memory sort
        of all order rows. Excludes orders that never shipped (NULL
        ``fulfilled_at``) — those would otherwise drag the average to
        inf. The OrderModel column is ``fulfilled_at`` (not
        ``shipped_at``); it's set when the order transitions to a
        fulfilled / shipped state.
        """
        delta_hours = (
            extract(
                "epoch",
                OrderModel.fulfilled_at - OrderModel.created_at,
            )
            / 3600.0
        ).label("hours")
        query = select(
            func.coalesce(func.avg(delta_hours), 0).label("avg_h"),
            func.coalesce(
                func.percentile_cont(0.5).within_group(delta_hours.asc()), 0
            ).label("p50_h"),
            func.coalesce(
                func.percentile_cont(0.95).within_group(delta_hours.asc()), 0
            ).label("p95_h"),
        ).where(
            *self._store_window(store_id, date_from, date_to),
            OrderModel.fulfilled_at.isnot(None),
        )
        result = await self.session.execute(self._tenant_filter(query))
        row = result.one()
        return {
            "avg_hours": round(float(row.avg_h or 0), 1),
            "p50_hours": round(float(row.p50_h or 0), 1),
            "p95_hours": round(float(row.p95_h or 0), 1),
        }

    async def orders_by_hour(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[int, int]:
        """Histogram by hour-of-day (0–23)."""
        hr = extract("hour", OrderModel.created_at).label("hour")
        query = (
            select(hr, func.count(OrderModel.id))
            .where(*self._store_window(store_id, date_from, date_to))
            .group_by(hr)
            .order_by(hr)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return {int(row[0]): int(row[1]) for row in result.all()}

    # ── Revenue breakdown — by fulfillment + payment status ────────
    async def revenue_by_fulfillment_status(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, dict]:
        query = (
            select(
                OrderModel.fulfillment_status,
                func.count(OrderModel.id).label("orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
            )
            .where(*self._store_window(store_id, date_from, date_to))
            .group_by(OrderModel.fulfillment_status)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return {
            (row[0].value if row[0] is not None else "unknown"): {
                "orders": int(row.orders),
                "revenue_cents": int(row.revenue_cents),
            }
            for row in result.all()
        }

    async def revenue_summary_paid(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, int]:
        """Aggregate gross/discounts/shipping for paid orders only.

        ``paid`` means ``payment_status IN (PAID, PARTIALLY_REFUNDED)`` —
        the same definition the in-memory loop used. All values in cents.
        """
        from src.core.entities.order import PaymentStatus  # avoid module cycle

        query = select(
            func.coalesce(func.sum(OrderModel.subtotal), 0).label("gross"),
            func.coalesce(func.sum(OrderModel.discount_amount), 0).label("discounts"),
            func.coalesce(func.sum(OrderModel.shipping_cost), 0).label("shipping"),
        ).where(
            OrderModel.store_id == store_id,
            OrderModel.created_at >= date_from,
            OrderModel.created_at <= date_to,
            OrderModel.status.notin_(_NON_REVENUE_STATUSES),
            OrderModel.payment_status.in_([
                PaymentStatus.PAID,
                PaymentStatus.PARTIALLY_REFUNDED,
            ]),
        )
        result = await self.session.execute(self._tenant_filter(query))
        row = result.one()
        return {
            "gross_cents": int(row.gross or 0),
            "discounts_cents": int(row.discounts or 0),
            "shipping_cents": int(row.shipping or 0),
        }

    async def coupon_usage(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """Group paid orders by coupon_code; sum uses + total discount."""
        from src.core.entities.order import PaymentStatus

        query = (
            select(
                OrderModel.coupon_code,
                func.count(OrderModel.id).label("uses"),
                func.coalesce(func.sum(OrderModel.discount_amount), 0).label(
                    "revenue_impact"
                ),
            )
            .where(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= date_from,
                OrderModel.created_at <= date_to,
                OrderModel.status.notin_(_NON_REVENUE_STATUSES),
                OrderModel.payment_status.in_([
                    PaymentStatus.PAID,
                    PaymentStatus.PARTIALLY_REFUNDED,
                ]),
                OrderModel.coupon_code.isnot(None),
            )
            .group_by(OrderModel.coupon_code)
            .order_by(func.count(OrderModel.id).desc())
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [
            {
                "code": row.coupon_code,
                "uses": int(row.uses),
                "revenue_impact": int(row.revenue_impact),
            }
            for row in result.all()
        ]

    async def revenue_by_payment_status(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, dict]:
        query = (
            select(
                OrderModel.payment_status,
                func.count(OrderModel.id).label("orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
            )
            .where(*self._store_window(store_id, date_from, date_to))
            .group_by(OrderModel.payment_status)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return {
            (row[0].value if row[0] is not None else "unknown"): {
                "orders": int(row.orders),
                "revenue_cents": int(row.revenue_cents),
            }
            for row in result.all()
        }

    # ── New vs returning customers ─────────────────────────────────
    async def new_vs_returning(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, int]:
        """Count distinct customers in the period, split by whether the
        period contains their FIRST order (new) or not (returning).

        Implemented as one CTE-style query: the inner select grabs the
        per-customer first-order timestamp across all time; the outer
        bucket compares it to ``date_from``.
        """
        first_order_per_customer = (
            select(
                OrderModel.customer_id.label("customer_id"),
                func.min(OrderModel.created_at).label("first_at"),
            )
            .where(
                OrderModel.store_id == store_id,
                OrderModel.status.notin_(_NON_REVENUE_STATUSES),
            )
            .group_by(OrderModel.customer_id)
        )
        first_order_per_customer = self._tenant_filter(first_order_per_customer)
        first_subq = first_order_per_customer.subquery()

        # Distinct customers active in the window, joined with their
        # first-order timestamp.
        active = (
            select(
                OrderModel.customer_id,
                first_subq.c.first_at,
            )
            .join(first_subq, OrderModel.customer_id == first_subq.c.customer_id)
            .where(*self._store_window(store_id, date_from, date_to))
            .distinct()
        )
        active = self._tenant_filter(active)
        active_subq = active.subquery()

        is_new = case(
            (active_subq.c.first_at >= date_from, "new"),
            else_="returning",
        )
        query = select(is_new, func.count()).group_by(is_new)
        result = await self.session.execute(query)
        out = {"new": 0, "returning": 0}
        for row in result.all():
            out[row[0]] = int(row[1])
        return out

    async def period_revenue_and_unique_customers(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, int]:
        """Total revenue + distinct customer count over the period.

        Used to compute average customer value without materializing the
        order list.
        """
        query = select(
            func.coalesce(func.sum(OrderModel.total), 0).label("revenue"),
            func.count(func.distinct(OrderModel.customer_id)).label("customers"),
        ).where(*self._store_window(store_id, date_from, date_to))
        result = await self.session.execute(self._tenant_filter(query))
        row = result.one()
        return {
            "revenue_cents": int(row.revenue or 0),
            "unique_customers": int(row.customers or 0),
        }

    # ── Daily revenue series (forecast fallback) ───────────────────
    async def daily_revenue_series(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """One row per UTC date that had at least one non-cancelled order.

        Fallback for the forecast endpoint when the daily-rollup task
        hasn't run yet for this store (brand-new merchants, or the
        cron hasn't fired since signup). Returns
        ``[{rollup_date, total_revenue_cents, total_orders}]`` so the
        route can wrap each row in a rollup-shaped object and feed it
        to the existing forecast service unchanged.
        """
        from sqlalchemy import Date as _Date

        day_expr = cast(OrderModel.created_at, _Date).label("day")
        query = (
            select(
                day_expr,
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
                func.count(OrderModel.id).label("orders"),
            )
            .where(*self._store_window(store_id, date_from, date_to))
            .group_by(day_expr)
            .order_by(day_expr)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [
            {
                "rollup_date": row.day,
                "total_revenue_cents": int(row.revenue_cents or 0),
                "total_orders": int(row.orders or 0),
            }
            for row in result.all()
        ]

    # ── COD outcomes (order-derived, not shipment-derived) ─────────
    async def cod_summary(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict:
        """COD outcome roll-up over the period, sourced from ``orders``.

        The previous version of this stat hung off the ``shipments``
        table, which is only populated when a merchant integrates a
        courier (Bosta / MyLerz / J&T). Stores that fulfil manually
        have COD orders in ``orders`` but no shipment rows, so the
        dashboard read zeros across the board. Pulling from ``orders``
        works for both flows because the courier webhooks already flip
        ``orders.status`` on delivery / return.

        ``rejected`` follows the original semantics: returned orders
        (customer refused on delivery) plus cancellations. Cancelled
        orders are still counted in the total because a COD cancellation
        is a real merchant signal — not a non-event.
        """
        from src.core.entities.order import OrderStatus

        delivered_filter = OrderModel.status == OrderStatus.DELIVERED
        returned_filter = OrderModel.status == OrderStatus.RETURNED
        rejected_filter = OrderModel.status.in_([
            OrderStatus.RETURNED,
            OrderStatus.CANCELLED,
        ])

        query = select(
            func.count().label("total"),
            func.count().filter(delivered_filter).label("delivered"),
            func.count().filter(returned_filter).label("returned"),
            func.count().filter(rejected_filter).label("rejected"),
            func.coalesce(func.sum(OrderModel.total), 0).label("total_cod_amount"),
            func.coalesce(func.sum(OrderModel.total).filter(rejected_filter), 0).label(
                "rejected_amount"
            ),
        ).where(
            OrderModel.store_id == store_id,
            OrderModel.payment_method == "cod",
            OrderModel.created_at >= date_from,
            OrderModel.created_at <= date_to,
        )
        result = await self.session.execute(self._tenant_filter(query))
        row = result.one()
        return {
            "total": int(row.total or 0),
            "delivered": int(row.delivered or 0),
            "returned": int(row.returned or 0),
            "rejected": int(row.rejected or 0),
            "total_cod_amount": int(row.total_cod_amount or 0),
            "rejected_amount": int(row.rejected_amount or 0),
        }

    async def cod_rejections_by_location(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """Per-city COD outcome split, normalized casing.

        Same data source as :py:meth:`cod_summary` — pulls directly
        from ``orders`` so manual-ship stores also show up. ``rejected``
        is RETURNED + CANCELLED to match the summary's definition.
        """
        from src.core.entities.order import OrderStatus

        rejected_filter = OrderModel.status.in_([
            OrderStatus.RETURNED,
            OrderStatus.CANCELLED,
        ])
        location_expr = func.nullif(
            func.lower(
                func.trim(
                    func.coalesce(
                        OrderModel.shipping_address["city"].astext,
                        OrderModel.shipping_address["state"].astext,
                        "",
                    )
                )
            ),
            "",
        ).label("location")

        query = (
            select(
                location_expr,
                func.count().label("total"),
                func.count().filter(rejected_filter).label("rejected"),
            )
            .where(
                OrderModel.store_id == store_id,
                OrderModel.payment_method == "cod",
                OrderModel.created_at >= date_from,
                OrderModel.created_at <= date_to,
                location_expr.isnot(None),
            )
            .group_by(location_expr)
            .having(func.count().filter(rejected_filter) > 0)
            .order_by(func.count().filter(rejected_filter).desc())
            .limit(20)
        )
        result = await self.session.execute(self._tenant_filter(query))
        # Key is "rate" (not "rejection_rate") to match
        # CodRejectionLocationResponse — that field uses the unprefixed
        # name because the model's parent already scopes the value to
        # rejections (the top-level CodRejectionStatsResponse keeps
        # the longer ``rejection_rate`` to disambiguate).
        return [
            {
                "location": row.location,
                "total": int(row.total or 0),
                "rejected": int(row.rejected or 0),
                "rate": (
                    round(int(row.rejected or 0) / int(row.total or 1) * 100, 1)
                    if row.total
                    else 0.0
                ),
            }
            for row in result.all()
        ]

    # ── Marketing attribution ──────────────────────────────────────
    @staticmethod
    def _channel_case():
        """SQL CASE that mirrors the Python ``_classify_channel`` rules."""
        src_l = func.lower(func.coalesce(OrderModel.utm_source, ""))
        med_l = func.lower(func.coalesce(OrderModel.utm_medium, ""))
        return case(
            (
                (OrderModel.utm_source.is_(None)) | (src_l == "direct"),
                "Direct",
            ),
            (med_l.in_(["cpc", "ppc", "paid", "ad"]), "Paid"),
            (
                src_l.in_([
                    "facebook",
                    "instagram",
                    "tiktok",
                    "twitter",
                    "x",
                    "snapchat",
                    "linkedin",
                ]),
                "Social",
            ),
            (med_l == "email", "Email"),
            (med_l == "referral", "Referral"),
            else_="Organic",
        )

    async def channel_attribution(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> tuple[list[dict], int]:
        """Group orders by classified channel.

        Returns ``(rows, attributed_orders_count)``. Each row is
        ``{channel, orders, revenue_cents}``. ``attributed_orders_count``
        is the number of orders with a non-direct utm_source — used by
        the visit-attribution ratio in the route.
        """
        channel_expr = self._channel_case().label("channel")
        attributed_filter = (OrderModel.utm_source.isnot(None)) & (
            func.lower(OrderModel.utm_source) != "direct"
        )

        query = (
            select(
                channel_expr,
                func.count(OrderModel.id).label("orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
            )
            .where(*self._store_window(store_id, date_from, date_to))
            .group_by(channel_expr)
            .order_by(func.coalesce(func.sum(OrderModel.total), 0).desc())
        )
        result = await self.session.execute(self._tenant_filter(query))
        rows = [
            {
                "channel": row.channel,
                "orders": int(row.orders),
                "revenue_cents": int(row.revenue_cents),
            }
            for row in result.all()
        ]

        # Single pass for attributed-orders count.
        attributed_q = (
            select(func.count())
            .select_from(OrderModel)
            .where(
                *self._store_window(store_id, date_from, date_to),
                attributed_filter,
            )
        )
        attributed_q = self._tenant_filter(attributed_q)
        attributed = (await self.session.execute(attributed_q)).scalar() or 0
        return rows, int(attributed)

    async def campaign_attribution(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
        limit: int = 20,
    ) -> list[dict]:
        """Top-N utm_campaigns by revenue, normalized casing."""
        campaign_expr = func.lower(func.trim(OrderModel.utm_campaign)).label("campaign")
        query = (
            select(
                campaign_expr,
                func.count(OrderModel.id).label("orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
            )
            .where(
                *self._store_window(store_id, date_from, date_to),
                OrderModel.utm_campaign.isnot(None),
            )
            .group_by(campaign_expr)
            .order_by(func.coalesce(func.sum(OrderModel.total), 0).desc())
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [
            {
                "campaign": row.campaign,
                "orders": int(row.orders),
                "revenue_cents": int(row.revenue_cents),
            }
            for row in result.all()
        ]

    # ── Customer segmentation inputs ───────────────────────────────
    async def customer_period_aggregates(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """One row per customer who ordered in the window.

        Each row has the data RFM/CLV needs: orders, total_spent_cents,
        first_order_at, last_order_at — computed in SQL so the scorer
        works on a customer-sized set instead of an order-sized one.
        Cancelled and refunded orders are excluded so they don't
        artificially inflate frequency or spend.
        """
        query = (
            select(
                OrderModel.customer_id,
                func.count(OrderModel.id).label("orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label("total_spent"),
                func.min(OrderModel.created_at).label("first_at"),
                func.max(OrderModel.created_at).label("last_at"),
            )
            .where(
                *self._store_window(store_id, date_from, date_to),
                OrderModel.customer_id.isnot(None),
            )
            .group_by(OrderModel.customer_id)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [
            {
                "customer_id": row.customer_id,
                "orders": int(row.orders),
                "total_spent_cents": int(row.total_spent),
                "first_at": row.first_at,
                "last_at": row.last_at,
            }
            for row in result.all()
        ]

    async def customer_first_order_all_time(
        self,
        store_id: UUID,
        customer_ids: list[UUID],
    ) -> dict[UUID, datetime]:
        """Per-customer first order timestamp across all time.

        Used by cohort analysis so a customer's cohort is anchored on
        their actual first purchase, not just their first one inside
        the analysis window.
        """
        if not customer_ids:
            return {}
        query = (
            select(
                OrderModel.customer_id,
                func.min(OrderModel.created_at).label("first_at"),
            )
            .where(
                OrderModel.store_id == store_id,
                OrderModel.customer_id.in_(customer_ids),
                OrderModel.status.notin_(_NON_REVENUE_STATUSES),
            )
            .group_by(OrderModel.customer_id)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return {row.customer_id: row.first_at for row in result.all()}

    async def customer_order_months(
        self,
        store_id: UUID,
        customer_ids: list[UUID],
    ) -> dict[UUID, set[str]]:
        """Per-customer set of YYYY-MM month buckets they ordered in.

        Cohort retention needs to know which months each customer
        revisited; this gives us a sparse map of (customer_id ->
        {months}) without paginating the order list.
        """
        if not customer_ids:
            return {}
        month_expr = func.to_char(
            func.date_trunc("month", OrderModel.created_at), "YYYY-MM"
        ).label("month")
        query = (
            select(OrderModel.customer_id, month_expr)
            .where(
                OrderModel.store_id == store_id,
                OrderModel.customer_id.in_(customer_ids),
                OrderModel.status.notin_(_NON_REVENUE_STATUSES),
            )
            .group_by(OrderModel.customer_id, month_expr)
        )
        result = await self.session.execute(self._tenant_filter(query))
        out: dict[UUID, set[str]] = {}
        for row in result.all():
            out.setdefault(row.customer_id, set()).add(row.month)
        return out

    async def daily_revenue_per_product(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
        product_ids: list[str] | None = None,
    ) -> dict[str, dict[str, int]]:
        """``{product_id: {YYYY-MM-DD: revenue_cents}}``.

        Powers the 7-day sparkline on product-performance. Optional
        ``product_ids`` filter scopes the unnest to just the products the
        caller cares about (e.g. the top 50) so we don't materialize a
        line-item per SKU across the entire catalog.
        """
        line_items_cte = select(
            OrderModel.id.label("order_id"),
            OrderModel.created_at.label("order_at"),
            func.jsonb_array_elements(OrderModel.line_items).label("li"),
        ).where(*self._store_window(store_id, date_from, date_to))
        line_items_cte = self._tenant_filter(line_items_cte).subquery()

        # See ``top_products`` for why this cast is required — the
        # subquery wrapper drops JSONB type info, so subscripting
        # ``c.li[...]`` raises ``Operator 'getitem' is not supported``
        # without it.
        li = cast(line_items_cte.c.li, JSONB)
        product_id_expr = li["product_id"].astext.label("product_id")
        revenue_per_line = func.coalesce(
            cast(li["total_price"].astext, Integer),
            cast(func.coalesce(li["unit_price"].astext, "0"), Integer)
            * cast(func.coalesce(li["quantity"].astext, "0"), Integer),
        )
        day_expr = func.to_char(line_items_cte.c.order_at, "YYYY-MM-DD").label("day")

        query = (
            select(
                product_id_expr,
                day_expr,
                func.sum(revenue_per_line).label("revenue_cents"),
            )
            .where(product_id_expr.isnot(None))
            .group_by(product_id_expr, day_expr)
        )
        if product_ids:
            query = query.where(product_id_expr.in_(product_ids))

        result = await self.session.execute(query)
        out: dict[str, dict[str, int]] = {}
        for row in result.all():
            out.setdefault(row.product_id, {})[row.day] = int(row.revenue_cents or 0)
        return out

    async def inventory_health(
        self,
        store_id: UUID,
    ) -> dict[str, int]:
        """Bucket the store's catalog into in/low/out-of-stock counts."""
        from src.infrastructure.database.models.tenant.product import ProductModel

        query = select(
            func.count()
            .filter(ProductModel.quantity > ProductModel.low_stock_threshold)
            .label("in_stock"),
            func.count()
            .filter(
                (ProductModel.quantity > 0)
                & (ProductModel.quantity <= ProductModel.low_stock_threshold)
            )
            .label("low_stock"),
            func.count().filter(ProductModel.quantity == 0).label("out_of_stock"),
        ).where(ProductModel.store_id == store_id)
        # Tenant filter on products
        tid = get_tenant_id()
        if tid:
            query = query.where(ProductModel.tenant_id == tid)
        result = await self.session.execute(query)
        row = result.one()
        return {
            "in_stock": int(row.in_stock or 0),
            "low_stock": int(row.low_stock or 0),
            "out_of_stock": int(row.out_of_stock or 0),
        }

    # ── Top products (line_items JSONB unnest) ─────────────────────
    async def top_products(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
        limit: int = 50,
    ) -> list[dict]:
        """Group by product_id pulled from each order's line_items JSONB.

        We unnest ``line_items`` via ``jsonb_array_elements`` and lateral
        join, then sum quantity and revenue per product. The whole
        operation is one round-trip — no Python iteration over orders.

        Falls back to ``unit_price * quantity`` when ``total_price`` is
        absent on legacy line items (older orders carried unit_price only).
        """
        line_items_cte = select(
            OrderModel.id.label("order_id"),
            func.jsonb_array_elements(OrderModel.line_items).label("li"),
        ).where(*self._store_window(store_id, date_from, date_to))
        line_items_cte = self._tenant_filter(line_items_cte).subquery()

        # ``jsonb_array_elements`` returns JSONB at the DB level, but
        # after ``.subquery()`` SQLAlchemy loses the type info — the
        # ``c.li`` column reads as a plain element with no ``[]``
        # operator. Cast back to JSONB so subscript expressions like
        # ``li["product_id"].astext`` lower to the Postgres ``->``
        # operator instead of raising ``Operator 'getitem' is not
        # supported on this expression``.
        li = cast(line_items_cte.c.li, JSONB)
        product_id_expr = li["product_id"].astext.label("product_id")
        product_name_expr = li["name"].astext.label("product_name")
        quantity_expr = cast(func.coalesce(li["quantity"].astext, "0"), Integer).label(
            "quantity"
        )
        # total_price first (line.total_price), else unit_price * quantity
        revenue_per_line = func.coalesce(
            cast(li["total_price"].astext, Integer),
            cast(func.coalesce(li["unit_price"].astext, "0"), Integer)
            * cast(func.coalesce(li["quantity"].astext, "0"), Integer),
        ).label("revenue_cents")

        query = (
            select(
                product_id_expr,
                func.max(product_name_expr).label("product_name"),
                func.sum(quantity_expr).label("units_sold"),
                func.sum(revenue_per_line).label("revenue_cents"),
                func.count(line_items_cte.c.order_id.distinct()).label("orders"),
            )
            .where(product_id_expr.isnot(None))
            .group_by(product_id_expr)
            .order_by(func.sum(revenue_per_line).desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [
            {
                "product_id": row.product_id,
                "product_name": row.product_name,
                "units_sold": int(row.units_sold or 0),
                "revenue_cents": int(row.revenue_cents or 0),
                "orders": int(row.orders or 0),
            }
            for row in result.all()
        ]

    # ── Per-campaign performance (feature 001) ──────────────────────
    async def campaign_performance(
        self,
        store_id: UUID,
        campaign_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict:
        """Per-campaign attribution rollup.

        Returns the totals block shaped per
        contracts/merchant-campaign-api.md:

            {
                "sessions": int,            # COUNT(DISTINCT session_fingerprint)
                "product_views": int,       #   where step='product_view'
                "add_to_cart": int,         #   where step='add_to_cart'
                "checkout_started": int,    #   where step='checkout_started'
                "orders": int,              # from orders (non-cancelled/refunded)
                "revenue_cents": int,       # SUM(orders.total)
                "average_order_value_cents": int,
                "conversion_rates": {       # ratios between adjacent stages
                    "session_to_atc": float,
                    "atc_to_checkout": float,
                    "checkout_to_order": float,
                    "session_to_order": float,
                },
                "top_products": [...],      # top 10 by revenue (line_items unnest)
            }

        All queries are tenant-scoped via ``_tenant_filter``. The
        funnel_events query uses the partial
        ``ix_funnel_events_store_campaign_created`` index installed by
        the feature-001 migration — verify with EXPLAIN before assuming
        constant-time on a hot table.

        SEC-001: caller is responsible for confirming the auth'd user
        has access to ``store_id``. SEC-006 / cross-tenant safety: the
        ``campaign_id`` is filtered alongside ``store_id`` so a probe
        with another store's campaign UUID resolves to empty (correct
        — no data leak).
        """
        # ── Funnel-event aggregates ──────────────────────────────
        funnel_step_expr = FunnelEventModel.step
        funnel_session_expr = func.count(
            func.distinct(FunnelEventModel.session_fingerprint)
        )
        funnel_query = (
            select(funnel_step_expr, funnel_session_expr)
            .where(
                FunnelEventModel.store_id == store_id,
                FunnelEventModel.campaign_id == campaign_id,
                FunnelEventModel.created_at >= date_from,
                FunnelEventModel.created_at <= date_to,
            )
            .group_by(funnel_step_expr)
        )
        # Tenant scoping for funnel queries — the existing repo uses
        # OrderModel-keyed _tenant_filter, so do an inline tenant
        # check here.
        tid = get_tenant_id()
        if tid:
            funnel_query = funnel_query.where(FunnelEventModel.tenant_id == tid)
        funnel_result = await self.session.execute(funnel_query)
        funnel_counts: dict[str, int] = {
            row[0]: int(row[1] or 0) for row in funnel_result.all()
        }
        sessions = sum(
            funnel_counts.get(step, 0)
            for step in ("page_view", "product_view", "add_to_cart", "checkout_started")
        )
        # The spec also accepts treating "sessions" as the union of
        # distinct fingerprints across all steps; the per-step rollup
        # above gives us the same number when at least one page_view
        # was recorded, which is the normal case. Use page_view as the
        # primary signal — falls back to whichever step has the
        # highest count if no page_views were recorded.
        sessions = max(
            funnel_counts.get("page_view", 0),
            funnel_counts.get("product_view", 0),
            funnel_counts.get("add_to_cart", 0),
            sessions,
        )
        product_views = funnel_counts.get("product_view", 0)
        add_to_cart = funnel_counts.get("add_to_cart", 0)
        checkout_started = funnel_counts.get("checkout_started", 0)

        # ── Order aggregates ─────────────────────────────────────
        order_query = select(
            func.count(OrderModel.id).label("orders"),
            func.coalesce(func.sum(OrderModel.total), 0).label("revenue_cents"),
        ).where(
            OrderModel.store_id == store_id,
            OrderModel.campaign_id == campaign_id,
            OrderModel.created_at >= date_from,
            OrderModel.created_at <= date_to,
            OrderModel.status.notin_(_NON_REVENUE_STATUSES),
        )
        order_query = self._tenant_filter(order_query)
        order_row = (await self.session.execute(order_query)).one()
        orders = int(order_row.orders or 0)
        revenue_cents = int(order_row.revenue_cents or 0)
        aov = revenue_cents // orders if orders > 0 else 0

        def _ratio(n: int, d: int) -> float:
            return (n / d) if d > 0 else 0.0

        conversion_rates = {
            "session_to_atc": _ratio(add_to_cart, sessions),
            "atc_to_checkout": _ratio(checkout_started, add_to_cart),
            "checkout_to_order": _ratio(orders, checkout_started),
            "session_to_order": _ratio(orders, sessions),
        }

        # ── Top products (top 10 by revenue) ─────────────────────
        # Reuse the line_items-unnest pattern from top_products, but
        # scope by (store_id, campaign_id, date_range).
        line_items_cte = select(
            OrderModel.id.label("order_id"),
            func.jsonb_array_elements(OrderModel.line_items).label("li"),
        ).where(
            OrderModel.store_id == store_id,
            OrderModel.campaign_id == campaign_id,
            OrderModel.created_at >= date_from,
            OrderModel.created_at <= date_to,
            OrderModel.status.notin_(_NON_REVENUE_STATUSES),
        )
        line_items_cte = self._tenant_filter(line_items_cte).subquery()
        li = cast(line_items_cte.c.li, JSONB)
        product_id_expr = li["product_id"].astext.label("product_id")
        product_name_expr = li["name"].astext.label("product_name")
        quantity_expr = cast(func.coalesce(li["quantity"].astext, "0"), Integer).label(
            "quantity"
        )
        revenue_per_line = func.coalesce(
            cast(li["total_price"].astext, Integer),
            cast(func.coalesce(li["unit_price"].astext, "0"), Integer)
            * cast(func.coalesce(li["quantity"].astext, "0"), Integer),
        ).label("revenue_cents")
        tp_query = (
            select(
                product_id_expr,
                func.max(product_name_expr).label("product_name"),
                func.sum(quantity_expr).label("units_sold"),
                func.sum(revenue_per_line).label("revenue_cents"),
                # Distinct order_id count — the "orders" field on the
                # per-product breakdown is supposed to be "how many
                # orders contained this product", not "how many units
                # were sold". The earlier implementation reused the
                # units_sold sum which inflated the count for any
                # product bought in qty > 1. Matches the pattern in
                # ``top_products`` higher up the file.
                func.count(line_items_cte.c.order_id.distinct()).label("orders"),
            )
            .where(product_id_expr.isnot(None))
            .group_by(product_id_expr)
            .order_by(func.sum(revenue_per_line).desc())
            .limit(10)
        )
        tp_result = await self.session.execute(tp_query)
        top_products = [
            {
                "product_id": row.product_id,
                "name": row.product_name,
                "orders": int(row.orders or 0),
                "revenue_cents": int(row.revenue_cents or 0),
            }
            for row in tp_result.all()
        ]

        return {
            "sessions": sessions,
            "product_views": product_views,
            "add_to_cart": add_to_cart,
            "checkout_started": checkout_started,
            "orders": orders,
            "revenue_cents": revenue_cents,
            "average_order_value_cents": aov,
            "conversion_rates": conversion_rates,
            "top_products": top_products,
        }

    # ── LTV-by-channel (first-touch acquisition cohort) ─────────────
    _LTV_GROUP_FIELDS: dict[str, str] = {
        "source": "utm_source",
        "medium": "utm_medium",
        "campaign": "utm_campaign",
    }

    async def ltv_by_channel(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
        group_by: str = "source",
    ) -> list[dict]:
        """Lifetime value cohort by first-touch acquisition channel.

        Customers acquired in ``[date_from, date_to]`` (their
        ``first_touch_at`` falls inside the window) are bucketed by the
        ``utm_source`` / ``utm_medium`` / ``utm_campaign`` stored on
        ``customers.first_touch_attribution`` and aggregated against the
        full lifetime of their orders. The order side is filtered to
        non-cancelled / non-refunded but is NOT date-windowed — that's
        the difference between this and ``traffic_sources`` (period
        revenue) vs. LTV (lifetime revenue of a cohort).

        Missing first-touch data falls into a ``"direct"`` bucket so
        organic / direct visitors aren't lost from the rollup. Customers
        whose every order was cancelled / refunded contribute zero
        revenue but still count toward ``customer_count`` (the cohort
        size doesn't depend on order outcomes).

        Returns per-channel rows; the caller derives ``avg_order_value``,
        ``orders_per_customer``, and ``ltv`` at the route layer where
        rounding policy lives.
        """
        if group_by not in self._LTV_GROUP_FIELDS:
            raise ValueError(
                f"group_by={group_by!r}; expected one of {list(self._LTV_GROUP_FIELDS)}"
            )
        json_field = self._LTV_GROUP_FIELDS[group_by]

        channel_expr = func.coalesce(
            func.nullif(
                func.lower(
                    func.trim(CustomerModel.first_touch_attribution[json_field].astext)
                ),
                "",
            ),
            "direct",
        ).label("channel")

        # LEFT JOIN so customers with zero non-cancelled orders still
        # contribute to the cohort count; their revenue / order count
        # come back as 0 from the COALESCE / COUNT.
        join_clause = (
            (OrderModel.customer_id == CustomerModel.id)
            & (OrderModel.store_id == store_id)
            & (OrderModel.status.notin_(_NON_REVENUE_STATUSES))
        )

        query = (
            select(
                channel_expr,
                func.count(func.distinct(CustomerModel.id)).label("customer_count"),
                func.count(OrderModel.id).label("total_orders"),
                func.coalesce(func.sum(OrderModel.total), 0).label(
                    "total_revenue_cents"
                ),
            )
            .select_from(CustomerModel)
            .outerjoin(OrderModel, join_clause)
            .where(
                CustomerModel.store_id == store_id,
                CustomerModel.first_touch_at.isnot(None),
                CustomerModel.first_touch_at >= date_from,
                CustomerModel.first_touch_at <= date_to,
            )
            .group_by(channel_expr)
            .order_by(func.coalesce(func.sum(OrderModel.total), 0).desc())
        )

        # Tenant scoping on the customer side is sufficient: the JOIN
        # already binds orders to the same store_id, so cross-tenant
        # rows can't sneak in through the order half.
        tid = get_tenant_id()
        if tid:
            query = query.where(CustomerModel.tenant_id == tid)

        result = await self.session.execute(query)
        return [
            {
                "channel": row.channel,
                "customer_count": int(row.customer_count or 0),
                "total_orders": int(row.total_orders or 0),
                "total_revenue_cents": int(row.total_revenue_cents or 0),
            }
            for row in result.all()
        ]

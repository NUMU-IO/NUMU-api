"""Celery task for nightly analytics rollup.

Aggregates daily metrics per store into the analytics_daily_rollups table.
Runs at 03:30 UTC daily, then backfills the previous 90 days every run so a
single recovery after beat downtime catches up months of history rather
than just a week.

Each (store, day) is processed in its own session — a SQL error on one
day used to poison the shared session and silently fail every later day
on every later store. Now isolated rollbacks contain the blast radius.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

# Window the daily Celery run backfills. Was 7 — too narrow when beat is
# down for any non-trivial stretch; older missing days were never filled
# by the next successful run. 90 covers a full quarter so a single tick
# repairs a long outage without an ops backfill.
DEFAULT_BACKFILL_DAYS = 90

# Cap how many failure rows the task return value carries back to the
# broker. Logs always have everything; this is just to keep the result
# JSON small.
_MAX_REPORTED_FAILURES = 50

_task_loop = None


def run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.calculate_analytics_rollups",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
)
def calculate_analytics_rollups_task(self, backfill_days: int = DEFAULT_BACKFILL_DAYS):
    """Calculate analytics rollups for all active stores.

    Args:
        backfill_days: How many days back from yesterday to (re)compute.
            The default 90 covers a quarter so a single run repairs a long
            beat outage. Pass an explicit smaller value for hot-path runs
            where you only care about the most recent days.
    """
    try:
        result = run_async(_calculate_all_rollups(backfill_days))
        logger.info(f"Analytics rollup complete: {result}")
        return result
    except Exception as exc:
        logger.exception("Analytics rollup failed")
        raise self.retry(exc=exc)


async def _calculate_all_rollups(backfill_days: int = DEFAULT_BACKFILL_DAYS) -> dict:
    """Calculate and persist daily rollups for all active stores."""
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    today = date.today()
    dates_to_process = [today - timedelta(days=i) for i in range(1, backfill_days + 1)]

    # Pull active stores in one short-lived session so the listing isn't
    # held open across the full backfill loop.
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoreModel.id, StoreModel.tenant_id).where(
                StoreModel.status == "ACTIVE"
            )
        )
        stores = result.all()

    stats = {
        "processed": len(stores),
        "days_written": 0,
        "errors": 0,
        "backfill_days": backfill_days,
        # (store_id, YYYY-MM-DD, error message) — capped by _MAX_REPORTED_FAILURES.
        "failures": [],
    }

    for store_row in stores:
        store_stats = await _backfill_store(
            store_row.tenant_id, store_row.id, dates_to_process
        )
        stats["days_written"] += store_stats["days_written"]
        stats["errors"] += store_stats["errors"]
        if store_stats["failures"] and len(stats["failures"]) < _MAX_REPORTED_FAILURES:
            room = _MAX_REPORTED_FAILURES - len(stats["failures"])
            stats["failures"].extend(store_stats["failures"][:room])

    if stats["errors"]:
        # One summary line so failures show up even when the broker
        # truncates the return value or the result backend is being
        # ignored. Individual per-day warnings are still emitted below.
        logger.warning(
            "analytics_rollup_partial_failure: "
            f"{stats['errors']} day(s) failed across {stats['processed']} stores; "
            f"first up to {_MAX_REPORTED_FAILURES} returned in stats['failures']"
        )

    return stats


async def _backfill_store(tenant_id, store_id, dates_to_process: list[date]) -> dict:
    """Compute and upsert rollups for one store across a list of dates.

    Each date runs in its own session. If one day's `_aggregate_day` or
    `_upsert_rollup` raises (bad data, connection blip, schema drift),
    that session is rolled back and the next date opens a fresh one — so
    the failure can't poison subsequent days the way the previous shared
    session did.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal

    out = {"days_written": 0, "errors": 0, "failures": []}

    for rollup_date in dates_to_process:
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    data = await _aggregate_day(session, store_id, rollup_date)
                    await _upsert_rollup(
                        session, tenant_id, store_id, rollup_date, data
                    )
            out["days_written"] += 1
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            logger.warning(
                "rollup_failed",
                extra={
                    "store_id": str(store_id),
                    "rollup_date": rollup_date.isoformat(),
                    "error": err_msg,
                },
            )
            out["errors"] += 1
            out["failures"].append((str(store_id), rollup_date.isoformat(), err_msg))

    return out


async def backfill_store_range(store_id, start_date: date, end_date: date) -> dict:
    """Backfill rollups for one store across [start_date, end_date] inclusive.

    Used by the admin endpoint and any one-off CLI to recover a known gap
    without waiting on the nightly tick. Resolves tenant_id from the
    store row so callers don't have to pass it.
    """
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    if end_date < start_date:
        raise ValueError(f"end_date {end_date} is before start_date {start_date}")

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(StoreModel.tenant_id).where(StoreModel.id == store_id)
            )
        ).first()
    if row is None:
        raise ValueError(f"Store {store_id} not found")
    tenant_id = row.tenant_id

    span = (end_date - start_date).days + 1
    dates_to_process = [start_date + timedelta(days=i) for i in range(span)]

    return await _backfill_store(tenant_id, store_id, dates_to_process)


async def _aggregate_day(
    session,
    store_id,
    rollup_date: date,
) -> dict:
    """Aggregate all metrics for one store on one day."""
    from sqlalchemy import String, and_, cast, func, select

    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.page_view import PageViewModel
    from src.infrastructure.database.models.tenant.refund import RefundModel
    from src.infrastructure.database.models.tenant.shipment import ShipmentModel

    day_start = datetime(
        rollup_date.year, rollup_date.month, rollup_date.day, tzinfo=UTC
    )
    day_end = day_start + timedelta(days=1)

    # ── Orders ──
    order_query = select(
        func.count().label("total_orders"),
        func.coalesce(func.sum(OrderModel.total), 0).label("total_revenue"),
        func.count()
        .filter(OrderModel.payment_status.in_(["paid", "partially_refunded"]))
        .label("paid_orders"),
        func.count().filter(OrderModel.status == "cancelled").label("cancelled_orders"),
    ).where(
        and_(
            OrderModel.store_id == store_id,
            OrderModel.created_at >= day_start,
            OrderModel.created_at < day_end,
            # The orderstatus enum has the lowercase value `payment_failed`
            # from an early migration. SQLAlchemy's default enum binding
            # would send the uppercase member name `PAYMENT_FAILED`, which
            # PG rejects with `invalid input value for enum`. Cast to text
            # so the comparison runs against the actual enum value.
            cast(OrderModel.status, String) != "payment_failed",
        )
    )
    order_result = await session.execute(order_query)
    order_row = order_result.one()

    total_orders = order_row.total_orders
    total_revenue = order_row.total_revenue
    paid_orders = order_row.paid_orders
    cancelled_orders = order_row.cancelled_orders
    avg_order_value = total_revenue // total_orders if total_orders > 0 else 0

    # ── Top products + location + UTM (from individual orders) ──
    # WHERE excludes the three statuses that never represent realized demand:
    # cancelled (merchant or customer killed it), refunded (returned), and
    # payment_failed (customer never completed payment). Anything else —
    # including pending COD that hasn't been delivered yet — counts toward
    # "what's selling". COD is the dominant payment method in Egypt and
    # often sits in pending for days; gating Top Products on payment_status
    # made the widget empty for COD-heavy stores even when orders existed.
    orders_query = select(
        OrderModel.line_items,
        OrderModel.shipping_address,
        OrderModel.utm_source,
        OrderModel.utm_medium,
        OrderModel.total,
        OrderModel.payment_status,
    ).where(
        and_(
            OrderModel.store_id == store_id,
            OrderModel.created_at >= day_start,
            OrderModel.created_at < day_end,
            # Exclude statuses that don't represent realized demand:
            # cancelled (killed), refunded (returned), payment_failed
            # (customer never completed payment). Cast to text because
            # the orderstatus enum has lowercase storage values from an
            # early migration; SQLAlchemy's default binding would send
            # the uppercase Python member names, which PG rejects with
            # `invalid input value for enum`.
            cast(OrderModel.status, String).notin_((
                "cancelled",
                "refunded",
                "payment_failed",
            )),
        )
    )
    orders_result = await session.execute(orders_query)
    orders_rows = orders_result.all()

    product_agg: dict[str, dict] = {}
    location_agg: dict[str, dict] = defaultdict(
        lambda: {"location": "", "revenue": 0, "orders": 0}
    )
    source_agg: dict[str, dict] = defaultdict(
        lambda: {"source": "", "medium": "", "orders": 0, "revenue": 0}
    )

    for row in orders_rows:
        is_paid = row.payment_status in ("paid", "partially_refunded")

        # Products — count any non-cancelled / non-refunded / non-failed
        # order regardless of payment_status. ``revenue_paid`` is split out
        # so a future "paid revenue only" view can read either total without
        # re-aggregating.
        if row.line_items:
            for item in row.line_items:
                pid = str(item.get("product_id", ""))
                if not pid:
                    continue
                if pid not in product_agg:
                    product_agg[pid] = {
                        "product_id": pid,
                        "name": item.get("product_name", ""),
                        "sku": item.get("sku"),
                        "quantity": 0,
                        "revenue": 0,
                        "revenue_paid": 0,
                    }
                qty = item.get("quantity", 0)
                # Older line items only carried unit_price + quantity; new
                # ones include total_price. Fall through gracefully so the
                # backfill of historical data doesn't show 0 revenue for
                # legacy orders.
                line_revenue = item.get(
                    "total_price",
                    item.get("unit_price", 0) * qty,
                )
                product_agg[pid]["quantity"] += qty
                product_agg[pid]["revenue"] += line_revenue
                if is_paid:
                    product_agg[pid]["revenue_paid"] += line_revenue

        # Location
        addr = row.shipping_address or {}
        loc = addr.get("state") or addr.get("city") or addr.get("governorate", "")
        if loc:
            location_agg[loc]["location"] = loc
            location_agg[loc]["revenue"] += row.total or 0
            location_agg[loc]["orders"] += 1

        # UTM sources
        src = row.utm_source or "direct"
        med = row.utm_medium or ""
        key = f"{src}|{med}"
        source_agg[key]["source"] = src
        source_agg[key]["medium"] = med
        source_agg[key]["orders"] += 1
        source_agg[key]["revenue"] += row.total or 0

    # Sort and take top 20 products
    top_products = sorted(
        product_agg.values(), key=lambda x: x["revenue"], reverse=True
    )[:20]

    # Sort locations by revenue
    revenue_by_location = sorted(
        location_agg.values(), key=lambda x: x["revenue"], reverse=True
    )[:20]

    # Sort sources by orders
    traffic_sources = sorted(
        source_agg.values(), key=lambda x: x["orders"], reverse=True
    )[:20]

    # ── Customers ──
    new_customers_query = select(func.count()).where(
        and_(
            CustomerModel.store_id == store_id,
            func.date(CustomerModel.created_at) == rollup_date,
        )
    )
    new_customers_result = await session.execute(new_customers_query)
    new_customers = new_customers_result.scalar_one()

    # Returning = customers who ordered today but were created before today
    returning_query = select(func.count(func.distinct(OrderModel.customer_id))).where(
        and_(
            OrderModel.store_id == store_id,
            OrderModel.created_at >= day_start,
            OrderModel.created_at < day_end,
            OrderModel.customer_id.isnot(None),
        )
    )
    returning_result = await session.execute(returning_query)
    unique_customers_today = returning_result.scalar_one()
    returning_customers = max(0, unique_customers_today - new_customers)

    # ── Page views ──
    pv_query = select(
        func.count().label("total_views"),
        func.count(func.distinct(PageViewModel.session_fingerprint)).label(
            "unique_visitors"
        ),
    ).where(
        and_(
            PageViewModel.store_id == store_id,
            PageViewModel.created_at >= day_start,
            PageViewModel.created_at < day_end,
        )
    )
    pv_result = await session.execute(pv_query)
    pv_row = pv_result.one()

    # ── COD shipments ──
    cod_query = select(
        func.count().label("cod_orders"),
        func.count().filter(ShipmentModel.status == "delivered").label("cod_delivered"),
        func.count()
        .filter(ShipmentModel.status.in_(["failed", "returned", "rejected"]))
        .label("cod_rejected"),
    ).where(
        and_(
            ShipmentModel.store_id == store_id,
            ShipmentModel.cod_amount > 0,
            ShipmentModel.created_at >= day_start,
            ShipmentModel.created_at < day_end,
        )
    )
    cod_result = await session.execute(cod_query)
    cod_row = cod_result.one()

    # ── Refunds ──
    refund_query = select(
        func.count().label("refund_count"),
        func.coalesce(func.sum(RefundModel.amount), 0).label("refund_amount"),
    ).where(
        and_(
            RefundModel.store_id == store_id,
            RefundModel.created_at >= day_start,
            RefundModel.created_at < day_end,
            RefundModel.status == "completed",
        )
    )
    refund_result = await session.execute(refund_query)
    refund_row = refund_result.one()

    return {
        "total_revenue_cents": total_revenue,
        "total_orders": total_orders,
        "paid_orders": paid_orders,
        "cancelled_orders": cancelled_orders,
        "avg_order_value_cents": avg_order_value,
        "new_customers": new_customers,
        "returning_customers": returning_customers,
        "total_page_views": pv_row.total_views,
        "unique_visitors": pv_row.unique_visitors,
        "cod_orders": cod_row.cod_orders,
        "cod_delivered": cod_row.cod_delivered,
        "cod_rejected": cod_row.cod_rejected,
        "refund_count": refund_row.refund_count,
        "refund_amount_cents": refund_row.refund_amount,
        "top_products_json": top_products,
        "revenue_by_location_json": revenue_by_location,
        "traffic_sources_json": traffic_sources,
    }


async def _upsert_rollup(
    session,
    tenant_id,
    store_id,
    rollup_date: date,
    data: dict,
) -> None:
    """Insert or update a rollup row."""
    from uuid import uuid4

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from src.infrastructure.database.models.tenant.analytics_rollup import (
        AnalyticsDailyRollupModel,
    )

    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "store_id": store_id,
        "rollup_date": rollup_date,
        **data,
    }

    stmt = pg_insert(AnalyticsDailyRollupModel).values(**values)
    update_cols = {
        k: v
        for k, v in values.items()
        if k not in ("id", "tenant_id", "store_id", "rollup_date")
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            AnalyticsDailyRollupModel.store_id,
            AnalyticsDailyRollupModel.rollup_date,
        ],
        set_=update_cols,
    )
    await session.execute(stmt)

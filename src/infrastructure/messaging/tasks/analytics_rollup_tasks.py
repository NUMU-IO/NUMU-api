"""Celery task for nightly analytics rollup.

Aggregates daily metrics per store into the analytics_daily_rollups table.
Runs at 3:30 AM UTC, before the health score task (4:00 AM UTC).
Backfills last 7 days to catch late-arriving events.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

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
def calculate_analytics_rollups_task(self):
    """Calculate analytics rollups for all active stores."""
    try:
        result = run_async(_calculate_all_rollups())
        logger.info(f"Analytics rollup complete: {result}")
        return result
    except Exception as exc:
        logger.exception("Analytics rollup failed")
        raise self.retry(exc=exc)


async def _calculate_all_rollups() -> dict:
    """Calculate and persist daily rollups for all active stores."""
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    stats = {"processed": 0, "days_written": 0, "errors": 0}

    # Backfill last 7 days to catch late events
    today = date.today()
    dates_to_process = [today - timedelta(days=i) for i in range(1, 8)]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoreModel.id, StoreModel.tenant_id).where(
                StoreModel.status == "ACTIVE"
            )
        )
        stores = result.all()

        for store_row in stores:
            store_id = store_row.id
            tenant_id = store_row.tenant_id
            stats["processed"] += 1

            for rollup_date in dates_to_process:
                try:
                    data = await _aggregate_day(session, store_id, rollup_date)
                    await _upsert_rollup(
                        session, tenant_id, store_id, rollup_date, data
                    )
                    stats["days_written"] += 1
                except Exception as e:
                    logger.warning(
                        f"Rollup failed for store {store_id} date {rollup_date}: {e}"
                    )
                    stats["errors"] += 1

        await session.commit()

    return stats


async def _aggregate_day(
    session,
    store_id,
    rollup_date: date,
) -> dict:
    """Aggregate all metrics for one store on one day."""
    from sqlalchemy import and_, func, select

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
            OrderModel.status != "payment_failed",
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
            OrderModel.status != "payment_failed",
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

        # Products
        if is_paid and row.line_items:
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
                    }
                product_agg[pid]["quantity"] += item.get("quantity", 0)
                product_agg[pid]["revenue"] += item.get("total_price", 0)

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

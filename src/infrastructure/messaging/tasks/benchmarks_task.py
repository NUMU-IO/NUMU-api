"""Nightly platform benchmark aggregation (AI-7).

Runs PLATFORM-LEVEL (public schema, no tenant context) at 03:50 UTC —
after the analytics rollup (03:30), before the intelligence sweep
(04:15). It aggregates *across* tenants by design, which is exactly
why it lives beside the rollup cron and never inside tenant-scoped
request paths.

Privacy invariants (enforced by benchmark_service):
- only stores that opted in (``settings.benchmarks_opt_in``) contribute
- store-level values never leave this task — only percentile cells are
  written, winsorized at P5/P95
- the k-anonymity floor (n ≥ 10) is applied at read time, so immature
  cells can accumulate while the platform grows without being shown
"""

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop = None


def _run(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(name="tasks.compute_platform_benchmarks", bind=True, max_retries=1)
def compute_platform_benchmarks_task(self):
    """Recompute the current period's benchmark cells."""
    try:
        result = _run(_compute())
        logger.info(f"platform_benchmarks_complete: {result}")
        return result
    except Exception as exc:
        logger.exception("platform_benchmarks_failed")
        raise self.retry(exc=exc, countdown=600)


async def _collect_store_metrics(session) -> list[dict]:
    """Per-store metric values for every opted-in active store.

    A handful of GROUP BY store_id passes over the whole platform —
    orders of magnitude cheaper than per-store loops, and no
    store-level value ever leaves this function's caller.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from src.application.services.benchmark_service import size_tier
    from src.core.entities.order import OrderStatus
    from src.infrastructure.database.models.tenant.funnel_event import (
        FunnelEventModel,
    )
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel

    now = datetime.now(UTC)
    d30, d90 = now - timedelta(days=30), now - timedelta(days=90)

    stores_q = select(StoreModel.id, StoreModel.settings).where(
        StoreModel.status == "ACTIVE"
    )
    opted_in: dict = {}
    for row in (await session.execute(stores_q)).all():
        settings = row.settings or {}
        if settings.get("benchmarks_opt_in") is True:
            opted_in[row.id] = settings.get("industry") or None
    if not opted_in:
        return []

    ids = list(opted_in.keys())

    # ── 30d orders: volume, AOV, refund rate ──
    o30_q = (
        select(
            OrderModel.store_id,
            func.count().label("orders"),
            func.coalesce(func.sum(OrderModel.total), 0).label("revenue"),
            func.count()
            .filter(OrderModel.status == OrderStatus.REFUNDED)
            .label("refunded"),
        )
        .where(
            OrderModel.store_id.in_(ids),
            OrderModel.created_at >= d30,
            OrderModel.status != OrderStatus.DRAFT,
        )
        .group_by(OrderModel.store_id)
    )
    o30 = {r.store_id: r for r in (await session.execute(o30_q)).all()}

    # ── 90d repeat rate ──
    per_cust = (
        select(
            OrderModel.store_id.label("store_id"),
            OrderModel.customer_id.label("customer_id"),
            func.count().label("orders"),
        )
        .where(
            OrderModel.store_id.in_(ids),
            OrderModel.created_at >= d90,
            OrderModel.customer_id.isnot(None),
            OrderModel.status.notin_([OrderStatus.CANCELLED, OrderStatus.REFUNDED]),
        )
        .group_by(OrderModel.store_id, OrderModel.customer_id)
        .subquery()
    )
    repeat_q = select(
        per_cust.c.store_id,
        func.count().label("customers"),
        func.count().filter(per_cust.c.orders >= 2).label("repeaters"),
    ).group_by(per_cust.c.store_id)
    repeat = {r.store_id: r for r in (await session.execute(repeat_q)).all()}

    # ── 90d COD door outcomes ──
    cod_q = (
        select(
            OrderModel.store_id,
            func.count().label("resolved"),
            func.count()
            .filter(OrderModel.status == OrderStatus.RETURNED)
            .label("returned"),
        )
        .where(
            OrderModel.store_id.in_(ids),
            OrderModel.created_at >= d90,
            OrderModel.payment_method == "cod",
            OrderModel.status.in_([OrderStatus.DELIVERED, OrderStatus.RETURNED]),
        )
        .group_by(OrderModel.store_id)
    )
    cod = {r.store_id: r for r in (await session.execute(cod_q)).all()}

    # ── 30d sessions (conversion denominator) ──
    sess_q = (
        select(
            FunnelEventModel.store_id,
            func.count(func.distinct(FunnelEventModel.session_fingerprint)).label(
                "sessions"
            ),
        )
        .where(
            FunnelEventModel.store_id.in_(ids),
            FunnelEventModel.created_at >= d30,
            FunnelEventModel.session_fingerprint.isnot(None),
        )
        .group_by(FunnelEventModel.store_id)
    )
    sessions = {
        r.store_id: int(r.sessions) for r in (await session.execute(sess_q)).all()
    }

    rows = []
    for store_id, industry in opted_in.items():
        o = o30.get(store_id)
        orders = int(o.orders) if o else 0
        if orders == 0:
            continue  # dormant this month — nothing meaningful to contribute
        rep = repeat.get(store_id)
        cd = cod.get(store_id)
        sess = sessions.get(store_id, 0)
        rows.append({
            "industry": industry,
            "size_tier": size_tier(orders),
            "metrics": {
                "aov_cents": int(o.revenue) / orders,
                "refund_rate_pct": int(o.refunded) / orders * 100,
                "repeat_rate_pct": (
                    int(rep.repeaters) / int(rep.customers) * 100
                    if rep and int(rep.customers) > 0
                    else None
                ),
                "cod_rejection_rate_pct": (
                    int(cd.returned) / int(cd.resolved) * 100
                    if cd and int(cd.resolved) >= 5
                    else None
                ),
                "conversion_rate_pct": (orders / sess * 100 if sess >= 100 else None),
            },
        })
    return rows


async def _compute() -> dict:
    from datetime import UTC, datetime

    from sqlalchemy import delete

    from src.application.services.benchmark_service import build_cells
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.platform_benchmark import (
        PlatformBenchmarkModel,
    )

    period = datetime.now(UTC).strftime("%Y-%m")

    async with AsyncSessionLocal() as session:
        async with session.begin():
            store_rows = await _collect_store_metrics(session)
            cells = build_cells(store_rows)

            # Idempotent per period: replace the month's cells wholesale.
            await session.execute(
                delete(PlatformBenchmarkModel).where(
                    PlatformBenchmarkModel.period == period
                )
            )
            for (segment_key, metric), cell in cells.items():
                session.add(
                    PlatformBenchmarkModel(
                        period=period,
                        segment_key=segment_key,
                        metric=metric,
                        p25=cell["p25"],
                        p50=cell["p50"],
                        p75=cell["p75"],
                        n_stores=cell["n_stores"],
                    )
                )

    return {
        "period": period,
        "contributing_stores": len(store_rows),
        "cells_written": len(cells),
    }

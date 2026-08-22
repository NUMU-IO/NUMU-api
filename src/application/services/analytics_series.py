"""Rollup-backed daily revenue series with a live top-up.

Shared by the analytics routes (KPI cards, sales chart, weekly digest
preview) and the scheduled weekly-digest task, so every surface that says
"revenue this week" agrees on the number.

Why this exists as a service rather than a route helper: the weekly digest
used to read ``analytics_daily_rollups`` only, while the KPI cards on the
same page used this rollup+live merge. The nightly rollup task deliberately
never writes *today*, so a store whose week's orders all landed today (or on
a day the beat never processed) got "No sales this week" printed directly
under "Booked sales EGP 729.45".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from src.core.utils.store_timezone import local_date, local_day_bounds
from src.infrastructure.repositories import AnalyticsRollupRepository, OrderRepository


def local_window_instants(
    start_d: date, end_d: date, tz_name: str
) -> tuple[datetime, datetime]:
    """Inclusive UTC instant bounds covering the local days ``[start_d, end_d]``.

    ``local_day_bounds`` returns a half-open ``[start, end)``; the repo
    queries here are inclusive on both ends, so the upper bound is pulled
    back by a microsecond rather than letting an order stamped exactly at
    the next local midnight leak into the window.
    """
    start_dt, _ = local_day_bounds(start_d, tz_name)
    _, end_excl = local_day_bounds(end_d, tz_name)
    return start_dt, end_excl - timedelta(microseconds=1)


async def daily_revenue_series(
    *,
    store_id: UUID,
    tz_name: str,
    rollup_repo: AnalyticsRollupRepository,
    order_repo: OrderRepository,
    start_d: date,
    end_d: date,
    today_local: date | None = None,
) -> dict[date, tuple[int, int]]:
    """``{local_date: (revenue_cents, order_count)}`` for a closed date range.

    ``today_local`` lets a caller that has already resolved the store-local
    date (or pinned the clock in a test) pass it in; otherwise it is taken
    from the wall clock.

    Rollup rows are used only for days that are already COMPLETE. Today —
    and any future date inside the window — is always computed live, and
    so is any day the nightly task never wrote.

    Why today can never come from the rollup: the task runs once at
    03:30 and writes a row for the day it runs in. Serving that row for
    the rest of the day freezes the number at whatever had happened by
    03:30.

    Also repairs gaps: any date in the window with no rollup row falls
    through to the same live query, so a beat outage degrades to "slightly
    slower" instead of "silently zero". The live query is a single indexed
    GROUP BY over the missing span only, not the whole window.
    """
    if today_local is None:
        today_local = local_date(datetime.now(UTC), tz_name)
    n = (end_d - start_d).days + 1
    if n <= 0:
        return {}
    all_dates = [start_d + timedelta(days=i) for i in range(n)]

    rollups = await rollup_repo.get_range(store_id, start_d, end_d)
    by_date: dict[date, tuple[int, int]] = {
        r.rollup_date: (r.total_revenue_cents or 0, r.total_orders or 0)
        for r in rollups
        if r.rollup_date < today_local
    }

    missing = [d for d in all_dates if d not in by_date]
    if missing:
        live_start, live_end = local_window_instants(
            min(missing), max(missing), tz_name
        )
        rows = await order_repo.get_daily_aggregates(
            store_id, live_start, live_end, timezone=tz_name
        )
        live = {d: (rev, cnt) for d, rev, cnt in rows}
        for d in missing:
            by_date[d] = live.get(d, (0, 0))

    return {d: by_date.get(d, (0, 0)) for d in all_dates}


async def window_totals(
    *,
    store_id: UUID,
    tz_name: str,
    rollup_repo: AnalyticsRollupRepository,
    order_repo: OrderRepository,
    start_d: date,
    end_d: date,
    today_local: date | None = None,
) -> tuple[int, int]:
    """``(revenue_cents, order_count)`` summed over ``[start_d, end_d]``."""
    series = await daily_revenue_series(
        store_id=store_id,
        tz_name=tz_name,
        rollup_repo=rollup_repo,
        order_repo=order_repo,
        start_d=start_d,
        end_d=end_d,
        today_local=today_local,
    )
    revenue = sum(rev for rev, _ in series.values())
    orders = sum(cnt for _, cnt in series.values())
    return int(revenue), int(orders)

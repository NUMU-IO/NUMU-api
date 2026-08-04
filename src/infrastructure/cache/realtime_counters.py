"""Real-time analytics Redis counters.

Fire-and-forget helpers — all calls are wrapped in try/except so they never
break the calling code path.

**Daily counters are keyed by the store's LOCAL CALENDAR DATE.**

They used to be keyed only by store id (``rt:{store_id}:views_today``) and
reset was delegated entirely to a 25-hour TTL — which was re-applied on
*every single write*:

    pipe.incr(views_key)
    pipe.expire(views_key, _DAY_TTL)   # ← pushed the expiry forward again

On any store receiving at least one page view every 25 hours the key
therefore never expired, and "views today" / "visitors today" / "orders
today" / "revenue today" became running totals since the key was first
created. They only ever went up. The counters were accurate only on stores
quiet enough to go a full day without a visitor — i.e. exactly the stores
nobody was watching. The same applied to every ``hourly_*`` bucket, so the
Live tab's 24-hour histogram was a multi-day pile-up rather than today.

Putting the local date in the key makes the rollover structural: at local
midnight every counter naturally starts from a fresh key, and the TTL goes
back to being what it should always have been — garbage collection, not
correctness.
"""

import json
import logging
from datetime import UTC, date, datetime
from uuid import UUID

from src.core.utils.store_timezone import (
    DEFAULT_STORE_TIMEZONE,
    local_date,
    safe_zone,
)
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = logging.getLogger(__name__)

# TTL on the dated keys. 48h (not 25h) so yesterday stays readable for a
# late-running comparison job; correctness no longer depends on it.
_DAY_TTL = 48 * 60 * 60
# Active visitor window.
_ACTIVE_TTL = 5 * 60


def _resolve_tz(tz_name: str | None) -> str:
    return tz_name or DEFAULT_STORE_TIMEZONE


def _local_today(tz_name: str | None) -> date:
    return local_date(datetime.now(UTC), _resolve_tz(tz_name))


def _key(store_id: UUID, suffix: str) -> str:
    """Undated key — for rolling structures that are not daily totals."""
    return f"rt:{store_id}:{suffix}"


def _day_key(store_id: UUID, suffix: str, tz_name: str | None) -> str:
    """Key scoped to the store's local calendar day."""
    return f"rt:{store_id}:{_local_today(tz_name).isoformat()}:{suffix}"


def _local_hour(tz_name: str | None) -> int:
    return datetime.now(safe_zone(_resolve_tz(tz_name))).hour


async def _get_client():
    cache = RedisCacheService()
    return await cache._get_client()


async def record_page_view(
    store_id: UUID,
    fingerprint: str | None,
    path: str | None = None,
    tz_name: str | None = None,
) -> None:
    """Bump today's view/visitor counters and mark the visitor active."""
    try:
        client = await _get_client()
        pipe = client.pipeline()

        views_key = _day_key(store_id, "views", tz_name)
        pipe.incr(views_key)
        pipe.expire(views_key, _DAY_TTL)

        if fingerprint:
            visitors_key = _day_key(store_id, "visitors", tz_name)
            pipe.pfadd(visitors_key, fingerprint)
            pipe.expire(visitors_key, _DAY_TTL)

            # Active visitors as a SORTED SET scored by timestamp, replacing
            # one SETEX key per fingerprint. Counting those required a
            # `SCAN MATCH rt:{store}:active:*` across the whole Redis
            # keyspace on every snapshot — and the SSE stream calls
            # get_snapshot every 5 seconds per connected merchant, so a
            # handful of open Live tabs kept the instance scanning
            # continuously. ZADD/ZCARD is O(log N) against one key.
            active_key = _key(store_id, "active")
            now_ts = datetime.now(UTC).timestamp()
            pipe.zadd(active_key, {fingerprint: now_ts})
            pipe.expire(active_key, _ACTIVE_TTL * 2)

        if path:
            pages_key = _day_key(store_id, "top_pages", tz_name)
            pipe.zincrby(pages_key, 1, path)
            pipe.expire(pages_key, _DAY_TTL)

        await pipe.execute()
    except Exception:
        logger.debug("realtime_counter_error", exc_info=True)


async def record_order_created(
    store_id: UUID, order_data: dict, tz_name: str | None = None
) -> None:
    """Bump today's order counter, push to the recent list, bucket by hour."""
    try:
        client = await _get_client()
        pipe = client.pipeline()

        orders_key = _day_key(store_id, "orders", tz_name)
        pipe.incr(orders_key)
        pipe.expire(orders_key, _DAY_TTL)

        # Deliberately NOT date-scoped: this is a rolling "latest 20", not a
        # daily total. Date-scoping it would blank the Live tab's order feed
        # at local midnight, which reads as an outage rather than a rollover.
        recent_key = _key(store_id, "recent_orders")
        pipe.lpush(recent_key, json.dumps(order_data))
        pipe.ltrim(recent_key, 0, 19)
        pipe.expire(recent_key, _DAY_TTL)

        hour = _local_hour(tz_name)
        hourly_orders_key = _day_key(store_id, f"hourly_orders:{hour}", tz_name)
        pipe.incr(hourly_orders_key)
        pipe.expire(hourly_orders_key, _DAY_TTL)

        total = order_data.get("total", 0)
        if total:
            hourly_rev_key = _day_key(store_id, f"hourly_revenue:{hour}", tz_name)
            pipe.incrby(hourly_rev_key, total)
            pipe.expire(hourly_rev_key, _DAY_TTL)

        await pipe.execute()
    except Exception:
        logger.debug("realtime_counter_error", exc_info=True)


async def record_payment(
    store_id: UUID, amount_cents: int, tz_name: str | None = None
) -> None:
    """Add to today's collected-revenue counter."""
    try:
        client = await _get_client()
        pipe = client.pipeline()

        revenue_key = _day_key(store_id, "revenue", tz_name)
        pipe.incrby(revenue_key, amount_cents)
        pipe.expire(revenue_key, _DAY_TTL)

        await pipe.execute()
    except Exception:
        logger.debug("realtime_counter_error", exc_info=True)


_EMPTY_SNAPSHOT = {
    "views_today": 0,
    "visitors_today": 0,
    "active_now": 0,
    "orders_today": 0,
    "revenue_today": 0,
    "recent_orders": [],
    "hourly_orders": [],
    "hourly_revenue": [],
    "top_pages": [],
    # False when Redis could not be read. Without this the UI cannot tell a
    # genuinely quiet day from a cache outage — both rendered as a confident
    # row of zeros.
    "available": True,
}


async def get_snapshot(store_id: UUID, tz_name: str | None = None) -> dict:
    """Read all real-time counters for a store's current local day."""
    try:
        client = await _get_client()

        views_key = _day_key(store_id, "views", tz_name)
        visitors_key = _day_key(store_id, "visitors", tz_name)
        orders_key = _day_key(store_id, "orders", tz_name)
        revenue_key = _day_key(store_id, "revenue", tz_name)
        recent_key = _key(store_id, "recent_orders")
        pages_key = _day_key(store_id, "top_pages", tz_name)
        active_key = _key(store_id, "active")

        cutoff = datetime.now(UTC).timestamp() - _ACTIVE_TTL

        pipe = client.pipeline()
        pipe.get(views_key)  # 0
        pipe.pfcount(visitors_key)  # 1
        pipe.get(orders_key)  # 2
        pipe.get(revenue_key)  # 3
        pipe.lrange(recent_key, 0, 19)  # 4
        pipe.zrevrange(pages_key, 0, 9, withscores=True)  # 5
        pipe.zremrangebyscore(active_key, "-inf", cutoff)  # 6 — evict stale
        pipe.zcard(active_key)  # 7 — count what's left

        # Queued as TWO contiguous blocks, not interleaved. A pipeline
        # returns one result per command in command order, so the reads
        # below (`8 + h` and `8 + 24 + h`) are only correct if all 24
        # order keys are queued before the first revenue key. Interleaving
        # them here — while reading them as two blocks — put revenue
        # figures into odd `hourly_orders` slots and order counts into the
        # back half of `hourly_revenue`, silently scrambling the Live
        # tab's 24-hour histogram.
        for h in range(24):
            pipe.get(_day_key(store_id, f"hourly_orders:{h}", tz_name))  # 8+h
        for h in range(24):
            pipe.get(_day_key(store_id, f"hourly_revenue:{h}", tz_name))  # 8+24+h

        results = await pipe.execute()

        recent_orders = []
        for item in results[4] or []:
            try:
                recent_orders.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                pass

        top_pages = [
            {"path": page, "views": int(score)} for page, score in results[5] or []
        ]

        hourly_orders = [int(results[8 + h] or 0) for h in range(24)]
        hourly_revenue = [int(results[8 + 24 + h] or 0) for h in range(24)]

        return {
            "views_today": int(results[0] or 0),
            "visitors_today": int(results[1] or 0),
            "active_now": int(results[7] or 0),
            "orders_today": int(results[2] or 0),
            "revenue_today": int(results[3] or 0),
            "recent_orders": recent_orders,
            "hourly_orders": hourly_orders,
            "hourly_revenue": hourly_revenue,
            "top_pages": top_pages,
            "available": True,
        }
    except Exception:
        logger.debug("realtime_snapshot_error", exc_info=True)
        return {**_EMPTY_SNAPSHOT, "available": False}

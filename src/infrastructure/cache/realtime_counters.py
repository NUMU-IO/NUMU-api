"""Real-time analytics Redis counters.

Fire-and-forget helpers — all calls are wrapped in try/except
so they never break the calling code path.

Keys use `rt:{store_id}:` prefix and reset daily via TTL.
"""

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from src.infrastructure.cache.redis_cache import RedisCacheService

logger = logging.getLogger(__name__)

# TTL: 25 hours — enough to cover a full day with buffer
_DAY_TTL = 25 * 60 * 60
# Active visitor window
_ACTIVE_TTL = 5 * 60  # 5 minutes


def _key(store_id: UUID, suffix: str) -> str:
    return f"rt:{store_id}:{suffix}"


async def _get_client():
    cache = RedisCacheService()
    return await cache._get_client()


async def record_page_view(
    store_id: UUID, fingerprint: str | None, path: str | None = None
) -> None:
    """Increment views_today, PFADD to visitors_today, SETEX active_now, track top pages."""
    try:
        client = await _get_client()
        pipe = client.pipeline()

        views_key = _key(store_id, "views_today")
        pipe.incr(views_key)
        pipe.expire(views_key, _DAY_TTL)

        if fingerprint:
            visitors_key = _key(store_id, "visitors_today")
            pipe.pfadd(visitors_key, fingerprint)
            pipe.expire(visitors_key, _DAY_TTL)

            active_key = _key(store_id, f"active:{fingerprint}")
            pipe.setex(active_key, _ACTIVE_TTL, "1")

        # Track top pages
        if path:
            pages_key = _key(store_id, "top_pages_today")
            pipe.zincrby(pages_key, 1, path)
            pipe.expire(pages_key, _DAY_TTL)

        await pipe.execute()
    except Exception:
        logger.debug("realtime_counter_error", exc_info=True)


async def record_order_created(store_id: UUID, order_data: dict) -> None:
    """Increment orders_today, LPUSH to recent_orders, track hourly orders/revenue."""
    try:
        client = await _get_client()
        pipe = client.pipeline()

        orders_key = _key(store_id, "orders_today")
        pipe.incr(orders_key)
        pipe.expire(orders_key, _DAY_TTL)

        recent_key = _key(store_id, "recent_orders")
        pipe.lpush(recent_key, json.dumps(order_data))
        pipe.ltrim(recent_key, 0, 19)  # Keep only latest 20
        pipe.expire(recent_key, _DAY_TTL)

        # Track hourly orders and revenue
        hour = datetime.now(UTC).hour
        hourly_orders_key = _key(store_id, f"hourly_orders:{hour}")
        pipe.incr(hourly_orders_key)
        pipe.expire(hourly_orders_key, _DAY_TTL)

        total = order_data.get("total", 0)
        if total:
            hourly_rev_key = _key(store_id, f"hourly_revenue:{hour}")
            pipe.incrby(hourly_rev_key, total)
            pipe.expire(hourly_rev_key, _DAY_TTL)

        await pipe.execute()
    except Exception:
        logger.debug("realtime_counter_error", exc_info=True)


async def record_payment(store_id: UUID, amount_cents: int) -> None:
    """INCRBY revenue_today."""
    try:
        client = await _get_client()
        pipe = client.pipeline()

        revenue_key = _key(store_id, "revenue_today")
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
}


async def get_snapshot(store_id: UUID) -> dict:
    """Read all real-time counters for a store."""
    try:
        client = await _get_client()

        views_key = _key(store_id, "views_today")
        visitors_key = _key(store_id, "visitors_today")
        orders_key = _key(store_id, "orders_today")
        revenue_key = _key(store_id, "revenue_today")
        recent_key = _key(store_id, "recent_orders")
        pages_key = _key(store_id, "top_pages_today")

        pipe = client.pipeline()
        pipe.get(views_key)  # 0
        pipe.pfcount(visitors_key)  # 1
        pipe.get(orders_key)  # 2
        pipe.get(revenue_key)  # 3
        pipe.lrange(recent_key, 0, 19)  # 4
        pipe.zrevrange(pages_key, 0, 9, withscores=True)  # 5: top 10 pages

        # Hourly orders/revenue for hours 0-23
        for h in range(24):
            pipe.get(_key(store_id, f"hourly_orders:{h}"))  # 6+h
            pipe.get(_key(store_id, f"hourly_revenue:{h}"))  # 6+24+h

        results = await pipe.execute()

        # Count active visitors
        active_count = 0
        active_pattern = _key(store_id, "active:*")
        async for _ in client.scan_iter(match=active_pattern, count=100):
            active_count += 1

        recent_orders = []
        for item in results[4] or []:
            try:
                recent_orders.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                pass

        # Top pages
        top_pages = []
        for page, score in results[5] or []:
            top_pages.append({"path": page, "views": int(score)})

        # Hourly data
        hourly_orders = [int(results[6 + h] or 0) for h in range(24)]
        hourly_revenue = [int(results[6 + 24 + h] or 0) for h in range(24)]

        return {
            "views_today": int(results[0] or 0),
            "visitors_today": int(results[1] or 0),
            "active_now": active_count,
            "orders_today": int(results[2] or 0),
            "revenue_today": int(results[3] or 0),
            "recent_orders": recent_orders,
            "hourly_orders": hourly_orders,
            "hourly_revenue": hourly_revenue,
            "top_pages": top_pages,
        }
    except Exception:
        logger.debug("realtime_snapshot_error", exc_info=True)
        return dict(_EMPTY_SNAPSHOT)

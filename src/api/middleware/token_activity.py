"""Per-token request trail for personal access and Partner App tokens.

The API otherwise keeps only ``last_used_at`` per token, so "what is this
token doing?" meant stitching nginx and MCP logs together by timestamp. Every
request that presents a known token now leaves one entry here, including the
ones the auth dependency refuses (wrong store, missing scope, API access off)
— those refusals are exactly what an admin watching a token wants to see.

Entries live in one capped Redis list per token, newest first.
ponytail: last 1000 requests per token, 30-day idle expiry, lost if Redis is
flushed. Move to a Postgres table when we need longer history or queries
across tokens.

Partner App requests also land in the app's own log, which its partner reads
in the partner portal: one capped list per app (no IP, user agent, query,
body or token) plus one hash of counters per app and hour. Worst case per
active app: APP_KEEP entries of ~200 bytes (~1 MB) plus 15 days of hourly
hashes (~150 KB). Hashes expire on their own; ``trim_app_logs`` (daily beat
task) drops list entries older than APP_RETENTION_DAYS.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

from fastapi import Request

from src.api.middleware.rate_limit import _get_cache, _get_client_ip, _pat_bucket
from src.core.logging import get_logger

logger = get_logger(__name__)

KEEP = 1000
IDLE_TTL_SECONDS = 30 * 24 * 3600
APP_KEEP = 5000
APP_RETENTION_DAYS = 14
LATENCY_BUCKETS_MS = (25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)

_background: set[asyncio.Task] = set()
#: Token bucket -> app, so a 429 from the rate limiter (which answers before
#: the auth dependency names the app) still reaches the app's log.
_app_by_bucket: dict[str, dict] = {}


def _key(token_id: str) -> str:
    return f"api_token_log:{token_id}"


def app_log_key(app_id: str) -> str:
    return f"app_api_log:{app_id}"


def app_hour_key(app_id: str, hour: datetime) -> str:
    return f"app_api_agg:{app_id}:{hour:%Y%m%d%H}"


def record_in_background(
    request: Request, status_code: int, duration_ms: float
) -> None:
    """Fire-and-forget ``record_token_request``: the response never waits."""
    task = asyncio.create_task(record_token_request(request, status_code, duration_ms))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _app_of(request: Request, pat: dict | None, status_code: int) -> dict | None:
    bucket = _pat_bucket(request)
    if pat and pat.get("app_id"):
        if bucket:
            if len(_app_by_bucket) > 10_000:
                _app_by_bucket.clear()
            _app_by_bucket[bucket] = pat
        return pat
    if status_code == 429 and bucket:
        return _app_by_bucket.get(bucket)
    return None


def _route(request: Request) -> str:
    route = getattr(request, "scope", {}).get("route")
    return getattr(route, "path", None) or request.url.path


async def record_token_request(
    request: Request, status_code: int, duration_ms: float
) -> None:
    """Append this request to its token's trail and its app's log. Never raises."""
    pat = getattr(request.state, "pat", None)
    try:
        app = _app_of(request, pat, status_code)
    except Exception:
        app = None
    if not pat and not app:
        return
    try:
        client = await _get_cache()._get_client()
        async with client.pipeline(transaction=False) as pipe:
            if pat:
                query = request.url.query
                entry = {
                    "at": datetime.now(UTC).isoformat(),
                    "method": request.method,
                    "path": request.url.path + (f"?{query[:300]}" if query else ""),
                    "status": status_code,
                    "ip": _get_client_ip(request),
                    "user_agent": request.headers.get("user-agent", "")[:160],
                    "ms": duration_ms,
                }
                key = _key(pat["token_id"])
                pipe.lpush(key, json.dumps(entry))
                pipe.ltrim(key, 0, KEEP - 1)
                pipe.expire(key, IDLE_TTL_SECONDS)
            if app:
                _queue_app(pipe, request, app, status_code, duration_ms)
            await pipe.execute()
    except Exception:
        logger.warning(
            "api_token_activity_record_failed",
            token_id=(pat or app or {}).get("token_id"),
        )


def _queue_app(pipe, request: Request, app: dict, status_code: int, ms: float) -> None:
    now = datetime.now(UTC)
    app_id = app["app_id"]
    entry = {
        "t": round(now.timestamp(), 3),
        "id": getattr(request.state, "request_id", None),
        "m": request.method,
        "r": _route(request),
        "s": status_code,
        "ms": ms,
        "st": app.get("store_id"),
    }
    key = app_log_key(app_id)
    pipe.lpush(key, json.dumps(entry, separators=(",", ":")))
    pipe.ltrim(key, 0, APP_KEEP - 1)
    pipe.expire(key, APP_RETENTION_DAYS * 86400)
    hour = app_hour_key(app_id, now)
    pipe.hincrby(hour, "n", 1)
    if status_code >= 400:
        pipe.hincrby(hour, f"{status_code // 100}xx", 1)
    if status_code == 429:
        pipe.hincrby(hour, "429", 1)
    pipe.hincrby(hour, f"b{sum(ms > b for b in LATENCY_BUCKETS_MS)}", 1)
    pipe.expire(hour, (APP_RETENTION_DAYS + 1) * 86400)


async def recent_requests(token_id: str, limit: int = KEEP) -> list[dict]:
    """Newest-first entries for one token."""
    client = await _get_cache()._get_client()
    raw = await client.lrange(_key(token_id), 0, max(0, min(limit, KEEP) - 1))
    return [json.loads(r) for r in raw]


async def app_log_entries(app_id: str) -> list[dict]:
    """Newest-first entries of one app's API log (at most APP_KEEP)."""
    client = await _get_cache()._get_client()
    return [json.loads(r) for r in await client.lrange(app_log_key(app_id), 0, -1)]


async def app_hourly(
    app_ids: list[str], since: datetime, until: datetime
) -> dict[str, dict[str, int]]:
    """Hourly counters of each app summed between two instants."""
    hours = []
    t = since.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    while t <= until:
        hours.append(t)
        t += timedelta(hours=1)
    out: dict[str, dict[str, int]] = {a: {} for a in app_ids}
    if not hours or not app_ids:
        return out
    client = await _get_cache()._get_client()
    async with client.pipeline(transaction=False) as pipe:
        for app_id in app_ids:
            for h in hours:
                pipe.hgetall(app_hour_key(app_id, h))
        rows = await pipe.execute()
    for i, app_id in enumerate(app_ids):
        total = out[app_id]
        for row in rows[i * len(hours) : (i + 1) * len(hours)]:
            for k, v in (row or {}).items():
                total[k] = total.get(k, 0) + int(v)
    return out


def p95_from_buckets(counts: dict[str, int]) -> int | None:
    """Upper edge of the latency bucket holding the 95th percentile.

    ponytail: bucketed, so accurate to the bucket edge (the open last bucket
    reads as its lower edge); keep raw samples if exact percentiles matter.
    """
    size = len(LATENCY_BUCKETS_MS) + 1
    n = sum(counts.get(f"b{i}", 0) for i in range(size))
    if not n:
        return None
    seen = 0
    for i in range(size):
        seen += counts.get(f"b{i}", 0)
        if seen >= 0.95 * n:
            return LATENCY_BUCKETS_MS[min(i, size - 2)]
    return None


async def trim_app_logs(client, now: float | None = None) -> int:
    """Drop app log entries older than APP_RETENTION_DAYS; return how many."""
    cutoff = (now or time.time()) - APP_RETENTION_DAYS * 86400
    dropped = 0
    async for key in client.scan_iter(match="app_api_log:*", count=500):
        n = await client.llen(key)
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            raw = await client.lindex(key, mid)
            if raw is None or json.loads(raw)["t"] < cutoff:
                hi = mid
            else:
                lo = mid + 1
        if lo == 0:
            await client.delete(key)
        elif lo < n:
            await client.ltrim(key, 0, lo - 1)
        dropped += n - lo
    return dropped

"""Public API limits: who may call, how fast, how much, and what they used.

Everything a merchant API key (``numu_pat_``) request goes through after the
key itself checks out lives here, so the numbers and the Redis layout sit in
one file:

* **Policy** comes from entitlements (``api_access`` plus the ``api_*`` limit
  features), plan defaults + merchant overrides, never from plan names.
* **Rate limits** are fixed windows checked and counted by one Lua script in
  one Redis round trip, all-or-nothing: merchant per second (burst), merchant
  per minute, key per minute, merchant per endpoint category per minute, and
  the monthly quota. The merchant windows are what stop a merchant from
  multiplying its limit by minting more keys.
* **Usage** is aggregated in Redis per tenant per day and flushed to
  ``api_usage_daily`` by Celery. No row per request.

Why fixed windows: a sliding log costs memory per request, and a token bucket
makes ``X-RateLimit-Remaining``/``Reset`` hard to explain. A fixed window can
let through up to twice its limit across a boundary; the per-second window
caps how fast that can happen. It is also what the existing IP limiter uses.

Redis unavailable = fail open (the request is served, nothing is counted),
matching ``RateLimitMiddleware``. Losing the limiter must not take the API
down with it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entitlements import UNLIMITED
from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = get_logger(__name__)

# ------------------------------------------------------------------ #
# Entitlement keys
# ------------------------------------------------------------------ #

ACCESS = "api_access"
PER_MINUTE = "api_requests_per_minute"
PER_SECOND = "api_requests_per_second"
MONTHLY_QUOTA = "api_monthly_quota"
KEY_LIMIT = "api_key_limit"

# ------------------------------------------------------------------ #
# Endpoint categories
# ------------------------------------------------------------------ #

LIGHT_READ = "light_read"
STANDARD_WRITE = "standard_write"
HEAVY = "heavy"
BULK = "bulk"

_HEAVY_SEGMENTS = {"analytics", "reports", "report", "export", "exports", "insights"}
_BULK_SEGMENTS = {"bulk", "import", "imports", "batch"}
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def category_for(path: str, method: str) -> str:
    """Bucket a request by cost. Bulk before heavy: a bulk export is bulk."""
    segments = {s.lower() for s in path.split("/") if s}
    if segments & _BULK_SEGMENTS or any(s.startswith("bulk") for s in segments):
        return BULK
    if segments & _HEAVY_SEGMENTS:
        return HEAVY
    return LIGHT_READ if method.upper() in _READ_METHODS else STANDARD_WRITE


def category_limit(category: str, per_minute: int | None) -> int | None:
    """Per-minute limit for one category, derived from the merchant's rate.

    Reads get the whole rate; writes half; heavy and bulk a fixed small
    number, never more than the rate itself. Derived rather than separate
    entitlements until a merchant actually needs them tuned apart.
    """
    if per_minute is None:
        return None
    if category == STANDARD_WRITE:
        return max(1, per_minute // 2)
    if category == HEAVY:
        return min(10, per_minute)
    if category == BULK:
        return min(2, per_minute)
    return per_minute


# ------------------------------------------------------------------ #
# Policy
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class ApiPolicy:
    """A merchant's API allowance. ``None`` means unlimited."""

    allowed: bool
    per_minute: int | None
    per_second: int | None
    monthly_quota: int | None
    key_limit: int | None


_POLICY_TTL = 30
_redis: RedisCacheService | None = None


def _cache() -> RedisCacheService:
    global _redis
    if _redis is None:
        _redis = RedisCacheService()
    return _redis


def _as_limit(value: Any) -> int | None:
    return None if value == UNLIMITED else int(value)


async def compute_policy(session: AsyncSession, tenant: Any) -> ApiPolicy:
    """Resolve from entitlements (uncached; the entitlement snapshot is)."""
    from src.application.services.entitlement_service import EntitlementService

    ents = EntitlementService(session)
    return ApiPolicy(
        allowed=await ents.has(tenant, ACCESS),
        per_minute=_as_limit(await ents.limit(tenant, PER_MINUTE)),
        per_second=_as_limit(await ents.limit(tenant, PER_SECOND)),
        monthly_quota=_as_limit(await ents.limit(tenant, MONTHLY_QUOTA)),
        key_limit=_as_limit(await ents.limit(tenant, KEY_LIMIT)),
    )


async def policy_for_tenant(session: AsyncSession, tenant_id: UUID) -> ApiPolicy:
    """The policy the request path uses: cached ``_POLICY_TTL`` seconds, so a
    cached API call needs no tenant read. An admin override or plan change
    reaches running integrations within that window."""
    from src.infrastructure.database.models.public.tenant import TenantModel

    key = f"api:policy:{tenant_id}"
    cached = await _cache().get(key)
    if isinstance(cached, dict):
        return ApiPolicy(**cached)
    tenant = await session.get(TenantModel, tenant_id)
    if tenant is None:
        return ApiPolicy(False, 0, 0, 0, 0)
    policy = await compute_policy(session, tenant)
    await _cache().set(key, asdict(policy), expire=_POLICY_TTL)
    return policy


async def forget_policy(tenant_id: UUID | str) -> None:
    await _cache().delete(f"api:policy:{tenant_id}")


# ------------------------------------------------------------------ #
# Key metadata cache
# ------------------------------------------------------------------ #

_KEY_TTL = 60
MISS = "miss"


def _key_cache_key(token_hash: str) -> str:
    return f"api:key:{token_hash}"


async def cached_key(token_hash: str) -> dict | str | None:
    """Cached metadata for a live key, ``MISS`` for a known-bad hash, or None."""
    return await _cache().get(_key_cache_key(token_hash))


async def cache_key(token_hash: str, meta: dict | None) -> None:
    """Remember a live key's metadata, or that a hash matches nothing usable,
    so a client retrying a dead key costs Redis reads, not queries."""
    await _cache().set(_key_cache_key(token_hash), meta or MISS, expire=_KEY_TTL)


async def forget_key(token_hash: str) -> None:
    await _cache().delete(_key_cache_key(token_hash))


async def should_mark_used(token_id: str) -> bool:
    """True at most once a minute per key: ``last_used_at`` is minute-grained
    and was an UPDATE + commit on every request."""
    try:
        client = await _cache()._get_client()
        return bool(await client.set(f"api:key:used:{token_id}", 1, nx=True, ex=60))
    except Exception:
        return False


# ------------------------------------------------------------------ #
# Rate limits + quota
# ------------------------------------------------------------------ #

RATE_LIMITED = "rate_limit_exceeded"
QUOTA_EXCEEDED = "monthly_api_quota_exceeded"

# KEYS[i] is a counter, ARGV[i] its limit (-1 = unlimited), ARGV[n+i] its TTL.
# Returns {-1} when the quota counter (the last key) is missing, so the caller
# can seed it from Postgres; {i, current} when window i is full (nothing is
# counted); or {0, c1..cn} with the counts after this request.
_LUA = """
local n = #KEYS
if redis.call('EXISTS', KEYS[n]) == 0 then return {-1} end
for i = 1, n do
  local lim = tonumber(ARGV[i])
  if lim >= 0 then
    local cur = tonumber(redis.call('GET', KEYS[i]) or '0')
    if cur >= lim then return {i, cur} end
  end
end
local out = {0}
for i = 1, n do
  local c = redis.call('INCR', KEYS[i])
  if c == 1 then redis.call('EXPIRE', KEYS[i], tonumber(ARGV[n + i])) end
  out[i + 1] = c
end
return out
"""


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    code: str | None = None
    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None
    retry_after: int | None = None
    quota_limit: int | None = None
    quota_remaining: int | None = None

    def headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.limit is not None:
            out["X-RateLimit-Limit"] = str(self.limit)
            out["X-RateLimit-Remaining"] = str(max(0, self.remaining or 0))
            out["X-RateLimit-Reset"] = str(self.reset)
        if self.quota_limit is not None:
            out["X-API-Quota-Limit"] = str(self.quota_limit)
            out["X-API-Quota-Remaining"] = str(max(0, self.quota_remaining or 0))
        if self.retry_after is not None:
            out["Retry-After"] = str(self.retry_after)
        return out


def month_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


def quota_key(tenant_id: str, now: datetime) -> str:
    return f"api:quota:{tenant_id}:{month_key(now)}"


def _next_month(now: datetime) -> datetime:
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


def _windows(
    tenant_id: str, token_id: str, category: str, policy: ApiPolicy, now: datetime
) -> list[tuple[str, int | None, int]]:
    """(redis key, limit, ttl) per window; the quota is always last."""
    sec = int(now.timestamp())
    minute = sec // 60
    per_min = policy.per_minute
    return [
        (f"api:rl:s:{tenant_id}:{sec}", policy.per_second, 2),
        (f"api:rl:m:{tenant_id}:{minute}", per_min, 70),
        (f"api:rl:k:{token_id}:{minute}", per_min, 70),
        (
            f"api:rl:c:{tenant_id}:{category}:{minute}",
            category_limit(category, per_min),
            70,
        ),
        (
            quota_key(tenant_id, now),
            policy.monthly_quota,
            int((_next_month(now) - now).total_seconds()) + 7 * 86400,
        ),
    ]


async def _seed_quota(
    client: Any, session_factory: Any, tenant_id: str, now: datetime, ttl: int
) -> None:
    """Rebuild a missing month counter from the flushed history (a new month,
    or Redis lost it). SET NX: concurrent seeders agree on the first value."""
    used = 0
    try:
        async with session_factory() as session:
            used = await month_usage_from_db(session, UUID(tenant_id), now)
    except Exception:
        logger.warning("api_quota_seed_failed", tenant_id=tenant_id)
    await client.set(quota_key(tenant_id, now), used, nx=True, ex=ttl)


async def check_and_count(
    *,
    tenant_id: str,
    token_id: str,
    path: str,
    method: str,
    policy: ApiPolicy,
    now: datetime | None = None,
    session_factory: Any = None,
) -> LimitDecision:
    """Check every window for this request and count it only if all pass."""
    now = now or datetime.now(UTC)
    category = category_for(path, method)
    windows = _windows(tenant_id, token_id, category, policy, now)
    keys = [w[0] for w in windows]
    args = [(-1 if w[1] is None else w[1]) for w in windows] + [w[2] for w in windows]
    try:
        client = await _cache()._get_client()
        result = await client.eval(_LUA, len(keys), *keys, *args)
        if int(result[0]) == -1:
            if session_factory is None:
                from src.infrastructure.database.connection import AsyncSessionLocal

                session_factory = AsyncSessionLocal
            await _seed_quota(client, session_factory, tenant_id, now, windows[-1][2])
            result = await client.eval(_LUA, len(keys), *keys, *args)
    except Exception:
        logger.warning("api_rate_limit_unavailable", tenant_id=tenant_id)
        return LimitDecision(allowed=True)

    reset = (int(now.timestamp()) // 60 + 1) * 60
    status = int(result[0])
    quota = policy.monthly_quota
    if status == 0:
        counts = [int(c) for c in result[1:]]
        minute_windows = [
            (windows[i][1], counts[i]) for i in (1, 2, 3) if windows[i][1] is not None
        ]
        limit, used = (
            min(minute_windows, key=lambda w: w[0] - w[1])
            if minute_windows
            else (None, 0)
        )
        return LimitDecision(
            allowed=True,
            limit=limit,
            remaining=None if limit is None else limit - used,
            reset=reset,
            quota_limit=quota,
            quota_remaining=None if quota is None else quota - counts[4],
        )

    blocked = status - 1
    if blocked == len(windows) - 1:
        return LimitDecision(
            allowed=False,
            code=QUOTA_EXCEEDED,
            retry_after=int((_next_month(now) - now).total_seconds()),
            quota_limit=quota,
            quota_remaining=0,
        )
    retry = 1 if blocked == 0 else max(1, reset - int(now.timestamp()))
    return LimitDecision(
        allowed=False,
        code=RATE_LIMITED,
        limit=windows[blocked][1],
        remaining=0,
        reset=int(now.timestamp()) + retry,
        retry_after=retry,
    )


# ------------------------------------------------------------------ #
# Usage aggregation
# ------------------------------------------------------------------ #

USAGE_TTL = 3 * 86400
DIRTY = "api:usage:dirty"
_SEP = "|"


def usage_key(tenant_id: str, day: date) -> str:
    return f"api:usage:{tenant_id}:{day.strftime('%Y%m%d')}"


def queue_usage(
    pipe: Any,
    *,
    tenant_id: str,
    token_id: str,
    method: str,
    route: str,
    status: int,
    ms: float,
    now: datetime | None = None,
) -> None:
    """Add one request to the day's aggregate (on the caller's pipeline).

    One hash per tenant per day; fields are ``token|method|route|metric``.
    ``route`` is the route template, so field count stays bounded by the
    API surface, not by ids in URLs.
    """
    now = now or datetime.now(UTC)
    key = usage_key(tenant_id, now.date())
    base = _SEP.join((token_id, method.upper(), route[:200]))
    pipe.hincrby(key, f"{base}{_SEP}n", 1)
    pipe.hincrby(key, f"{base}{_SEP}ms", int(ms))
    if status == 429:
        pipe.hincrby(key, f"{base}{_SEP}429", 1)
    elif 400 <= status < 500:
        pipe.hincrby(key, f"{base}{_SEP}4xx", 1)
    elif status >= 500:
        pipe.hincrby(key, f"{base}{_SEP}5xx", 1)
    pipe.expire(key, USAGE_TTL)
    pipe.sadd(DIRTY, key)


_METRICS = {
    "n": "requests",
    "ms": "latency_ms_sum",
    "4xx": "errors_4xx",
    "5xx": "errors_5xx",
    "429": "throttled",
}


def parse_usage(raw: dict[str, str]) -> dict[tuple[str, str, str], dict[str, int]]:
    """``{(token, method, route): {requests, latency_ms_sum, ...}}``"""
    rows: dict[tuple[str, str, str], dict[str, int]] = {}
    for field, value in raw.items():
        parts = field.rsplit(_SEP, 1)
        head = parts[0].split(_SEP, 2)
        if len(parts) != 2 or len(head) != 3 or parts[1] not in _METRICS:
            continue
        row = rows.setdefault(
            (head[0], head[1], head[2]), dict.fromkeys(_METRICS.values(), 0)
        )
        row[_METRICS[parts[1]]] = int(value)
    return rows


async def usage_today(
    tenant_id: str, now: datetime | None = None
) -> dict[tuple[str, str, str], dict[str, int]]:
    now = now or datetime.now(UTC)
    try:
        client = await _cache()._get_client()
        return parse_usage(await client.hgetall(usage_key(tenant_id, now.date())))
    except Exception:
        return {}


async def quota_used(tenant_id: str, now: datetime | None = None) -> int | None:
    now = now or datetime.now(UTC)
    try:
        client = await _cache()._get_client()
        value = await client.get(quota_key(tenant_id, now))
        return None if value is None else int(value)
    except Exception:
        return None


async def month_usage_from_db(
    session: AsyncSession, tenant_id: UUID, now: datetime
) -> int:
    from src.infrastructure.database.models.public.api_usage import (
        ApiUsageDailyModel as U,
    )

    first = now.date().replace(day=1)
    total = await session.scalar(
        select(func.coalesce(func.sum(U.requests - U.throttled), 0)).where(
            U.tenant_id == tenant_id, U.day >= first
        )
    )
    return int(total or 0)


async def flush_usage(session: AsyncSession) -> int:
    """Copy the dirty day-hashes into ``api_usage_daily``. Returns rows written.

    Idempotent: Redis holds running totals, and the upsert keeps the larger
    of the stored and the new value, so running twice changes nothing and a
    hash that restarted from zero (Redis lost it) never lowers history. The
    key leaves the dirty set BEFORE it is read, so a request landing mid-flush
    re-marks it for the next run instead of being skipped.
    """
    from sqlalchemy.dialects.postgresql import insert

    from src.infrastructure.database.models.public.api_usage import (
        ApiUsageDailyModel as U,
    )

    client = await _cache()._get_client()
    written = 0
    for key in await client.smembers(DIRTY):
        await client.srem(DIRTY, key)
        _, _, tenant_id, day = key.split(":", 3)
        rows = parse_usage(await client.hgetall(key))
        if not rows:
            continue
        values = [
            {
                "tenant_id": UUID(tenant_id),
                "token_id": UUID(token),
                "day": datetime.strptime(day, "%Y%m%d").date(),
                "method": method,
                "route": route,
                **metrics,
            }
            for (token, method, route), metrics in rows.items()
        ]
        stmt = insert(U).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "day", "token_id", "method", "route"],
            set_={
                col: func.greatest(getattr(U, col), getattr(stmt.excluded, col))
                for col in _METRICS.values()
            }
            | {"updated_at": func.now()},
        )
        await session.execute(stmt)
        written += len(values)
    await session.commit()
    return written

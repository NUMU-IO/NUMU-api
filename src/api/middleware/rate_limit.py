"""Redis-backed rate limiting middleware.

Uses Redis INCR + EXPIRE for distributed sliding-window counters.
Falls back to allowing requests when Redis is unavailable.

Tiers:
- auth:     5/min  (login, register, refresh)
- checkout: 10/min (storefront checkout)
- general:  100/min (authenticated) / 60/min (anonymous)
"""

import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = get_logger(__name__)

# Lazy-initialised Redis client (created on first request)
_cache: RedisCacheService | None = None


def _get_cache() -> RedisCacheService:
    global _cache
    if _cache is None:
        _cache = RedisCacheService()
    return _cache


# ------------------------------------------------------------------ #
# Endpoint sets
# ------------------------------------------------------------------ #

AUTH_ENDPOINTS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/public/auth/login",
    "/api/v1/public/auth/register",
    "/api/v1/public/auth/refresh",
    "/api/v1/storefront/auth/login",
    "/api/v1/storefront/auth/register",
    "/api/v1/storefront/customers/login",
    "/api/v1/storefront/customers/register",
}

SKIP_RATE_LIMIT = {
    "/",
    "/health",
    "/api/v1/health",
    "/api/v1/public/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def _is_checkout(path: str) -> bool:
    """Check if the path is a storefront checkout endpoint."""
    return path.startswith("/api/v1/storefront/store/") and path.endswith("/checkout")


# ------------------------------------------------------------------ #
# Redis sliding-window check
# ------------------------------------------------------------------ #


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting proxy headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    if request.client:
        return request.client.host

    return "unknown"


async def _check_rate_limit(ip: str, tier: str, limit: int) -> tuple[bool, int, int]:
    """Check whether the request is within the rate limit.

    Returns (is_allowed, current_count, retry_after_seconds).
    """
    window = int(time.time()) // 60
    key = f"ratelimit:{ip}:{tier}:{window}"

    try:
        cache = _get_cache()
        client = await cache._get_client()
        count = await client.incr(key)
        if count == 1:
            # First request in this window — set TTL (90s = 60s window + 30s buffer)
            await client.expire(key, 90)
        is_allowed = count <= limit
        retry_after = 60 - (int(time.time()) % 60) if not is_allowed else 0
        return is_allowed, count, retry_after
    except Exception:
        # Redis unavailable — degrade gracefully, allow the request
        logger.debug("redis_unavailable_rate_limit_skipped", tier=tier)
        return True, 0, 0


# ------------------------------------------------------------------ #
# Middleware
# ------------------------------------------------------------------ #


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce per-IP rate limits using Redis counters."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path

        if path in SKIP_RATE_LIMIT:
            return await call_next(request)

        # Determine tier and limit
        if path in AUTH_ENDPOINTS:
            tier = "auth"
            limit = settings.rate_limit_auth_requests_per_minute
        elif _is_checkout(path):
            tier = "checkout"
            limit = settings.rate_limit_checkout_requests_per_minute
        else:
            tier = "general"
            has_auth = "authorization" in request.headers
            if has_auth:
                limit = settings.rate_limit_requests_per_minute
            else:
                limit = settings.rate_limit_anon_requests_per_minute

        client_ip = _get_client_ip(request)
        is_allowed, count, retry_after = await _check_rate_limit(client_ip, tier, limit)

        if not is_allowed:
            logger.warning(
                "rate_limit_exceeded",
                ip=client_ip,
                path=path,
                tier=tier,
                count=count,
                limit=limit,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": "Too many requests. Please slow down.",
                    "code": "RATE_LIMIT_EXCEEDED",
                    "details": {"retry_after": retry_after},
                },
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        return response

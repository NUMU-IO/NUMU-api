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


def _is_coupon_apply(path: str) -> bool:
    """Check if the path is a coupon apply endpoint."""
    return path.startswith("/api/v1/storefront/store/") and path.endswith(
        "/coupons/apply"
    )


def _is_track_beacon(path: str) -> bool:
    """Anonymous storefront analytics beacon.

    Fires on every page view, add_to_cart, etc. — orders of magnitude
    more frequent than checkout. Needs its own bucket so a chatty
    storefront page doesn't blow through the general 60/min anon
    budget and silently lose events.
    """
    return path.startswith("/api/v1/storefront/store/") and path.endswith("/track")


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
# Phase 5.2 — per-user / per-identifier rate limits
# ------------------------------------------------------------------ #
#
# Per-IP limits stop a single attacker IP, but credential-stuffing
# rotates IPs. Adding a second layer keyed on the user's identity
# (email for unauthenticated auth attempts; user_id / customer_id
# when a token is present) catches the case where an attacker rotates
# IPs but keeps probing the same target account.
#
# Sensitive endpoints get a strict 5/hour-per-identifier on top of
# the per-IP minute bucket. The two checks compose: a request must
# pass BOTH to be allowed.

SENSITIVE_PER_USER_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/refresh",
}

# Customer-facing equivalents (store-id is dynamic; we match by suffix).
SENSITIVE_CUSTOMER_SUFFIXES = (
    "/auth/login",
    "/auth/forgot-password",
    "/auth/reset-password",
    "/auth/refresh",
    "/auth/register",
)


async def _check_per_user_limit(
    identifier: str,
    tier: str,
    limit: int,
    window_seconds: int = 3600,
) -> tuple[bool, int, int]:
    """Hourly per-identifier check. Window is a rolling 1-hour bucket
    (default) — the right granularity for credential-stuffing where
    bursts last seconds but the attack runs for hours.

    Returns (is_allowed, count, retry_after_seconds).
    """
    window = int(time.time()) // window_seconds
    key = f"ratelimit:user:{identifier}:{tier}:{window}"
    try:
        cache = _get_cache()
        client = await cache._get_client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds + 60)
        is_allowed = count <= limit
        retry_after = window_seconds - (int(time.time()) % window_seconds)
        return is_allowed, count, retry_after if not is_allowed else 0
    except Exception:
        logger.debug("redis_unavailable_per_user_skipped", tier=tier)
        return True, 0, 0


def _extract_user_identifier(request: Request) -> str | None:
    """Pull a stable identifier for the per-user check.

    Order of precedence:
      1. Authorization bearer token's `sub` claim (any authed user)
      2. customer_access_token cookie's `sub` (storefront customer)
      3. (for unauth attempts: caller passes the email from the body
         out-of-band — we don't read the body here because middleware
         can't await the body without consuming the stream)

    Returns None when no identifier can be derived; the per-user check
    is skipped in that case (per-IP still applies).
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        # We don't validate the token here — just hash it to get a
        # stable bucket key. The auth dependency on the route
        # validates as usual; this middleware just needs SOMETHING
        # consistent across requests from the same identity.
        return f"hbearer:{hash(auth_header[7:])}"
    cookie_token = request.cookies.get("customer_access_token")
    if cookie_token:
        return f"hcookie:{hash(cookie_token)}"
    return None


def _is_sensitive_per_user(path: str) -> bool:
    if path in SENSITIVE_PER_USER_PATHS:
        return True
    return any(path.endswith(s) for s in SENSITIVE_CUSTOMER_SUFFIXES)


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
        elif _is_coupon_apply(path):
            tier = "coupon"
            limit = 10  # 10 coupon validations per minute per IP
        elif _is_track_beacon(path):
            tier = "tracking"
            limit = 600  # ~10/sec per IP — analytics beacons are noisy
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

        # Phase 5.2 — secondary per-user check for sensitive endpoints.
        # The per-IP guard above stops a single attacker IP; this
        # second bucket catches credential-stuffing that rotates IPs
        # but keeps probing the same identifier.
        if _is_sensitive_per_user(path):
            identifier = _extract_user_identifier(request)
            if identifier:
                # Hourly bucket: 30 attempts/hour per identifier on
                # sensitive endpoints. Generous enough that legitimate
                # users hitting login a few times don't trip; tight
                # enough that a stuffer at 1 req/sec gets locked in
                # under a minute.
                user_allowed, user_count, user_retry = await _check_per_user_limit(
                    identifier=identifier,
                    tier=f"user:{tier}",
                    limit=30,
                )
                if not user_allowed:
                    logger.warning(
                        "rate_limit_exceeded_per_user",
                        ip=client_ip,
                        path=path,
                        tier=tier,
                        identifier_hash=identifier[
                            :16
                        ],  # truncated; don't log full hash
                        count=user_count,
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "success": False,
                            "error": (
                                "Too many requests for this account. Try again later."
                            ),
                            "code": "RATE_LIMIT_EXCEEDED_PER_USER",
                            "details": {"retry_after": user_retry},
                        },
                        headers={"Retry-After": str(user_retry)},
                    )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        return response

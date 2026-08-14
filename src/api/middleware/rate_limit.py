"""Redis-backed rate limiting middleware.

Uses Redis INCR + EXPIRE for distributed sliding-window counters.
Falls back to allowing requests when Redis is unavailable.

Tiers:
- auth:     5/min  (login, register, refresh)
- checkout: 10/min (storefront checkout)
- general:  100/min (authenticated) / 60/min (anonymous)
"""

import hashlib
import ipaddress
import time
from uuid import UUID

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.config import settings
from src.core.logging import get_logger
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

# Exact-match auth endpoints (static paths only — every entry here MUST
# correspond to a mounted route; tests/security/test_rate_limiting.py
# asserts that against the app's route table). Dynamic store-scoped
# customer auth routes cannot live in this set — see _is_auth_endpoint.
# 2026-07-21: pruned 7 dead entries (/api/v1/public/auth/* and
# /api/v1/storefront/{auth,customers}/* shapes) that matched no mounted
# route, and added the admin login/refresh which were missing — both
# verified against the live route table.
AUTH_ENDPOINTS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/admin/auth/login",
    "/api/v1/admin/auth/refresh",
}

# Store-scoped customer auth routes (/api/v1/storefront/store/{store_id}/auth/…)
# have a dynamic store id, so an exact-string set can never match them.
# Mirrors the merchant surface above: login/register/refresh.
CUSTOMER_AUTH_SUFFIXES = (
    "/auth/login",
    "/auth/register",
    "/auth/refresh",
)

SKIP_RATE_LIMIT = {
    "/",
    "/health",
    "/api/v1/health",
    "/api/v1/public/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def _is_auth_endpoint(path: str) -> bool:
    """Check if the path gets the strict auth tier.

    Exact merchant/admin endpoints plus the store-scoped customer auth
    routes. The customer routes carry a dynamic store id, so before this
    matcher existed they fell through to the general tier — giving
    customer-credential brute force 12x the intended per-IP budget
    (60/min anon vs 5/min auth).
    """
    if path in AUTH_ENDPOINTS:
        return True
    return path.startswith("/api/v1/storefront/store/") and path.endswith(
        CUSTOMER_AUTH_SUFFIXES
    )


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


def _is_track_lookup(path: str) -> bool:
    """Guest order lookup — the one brute-forceable tracking surface.

    ``GET /storefront/track/{order_id}`` is protected by 128 bits of UUID,
    but the lookup POST takes a short, sequential order number plus a
    phone/email. An attacker holding a leaked phone list can walk the
    number space, so it needs its own tight bucket — and specifically must
    NOT inherit the sibling ``/track`` beacon's 600/min tier, which
    ``_is_track_beacon`` would never grant it (that matcher needs an exact
    ``/track`` suffix) but a future refactor easily could.
    """
    return path.startswith("/api/v1/storefront/store/") and path.endswith(
        "/track/lookup"
    )


def _is_otp(path: str) -> bool:
    """Checkout-identity OTP issue/verify (checkout-identity feature).

    Issue sends a real WhatsApp message (cost + the merchant's number's
    reputation); verify is a 6-digit-code oracle. Both need a bucket far
    tighter than the anonymous general tier. The per-phone ceilings live in
    the route (Redis, 5/hr + 45s cooldown) — this per-IP tier is the layer
    an attacker rotating phones can't sidestep.
    """
    return path.startswith("/api/v1/storefront/store/") and (
        path.endswith("/identity/otp/issue") or path.endswith("/identity/otp/verify")
    )


def _is_whatsapp_byo_connect(path: str) -> bool:
    """backend-030 / TASK-SEC-003 — BYO connect hits Meta with 3 reads
    per attempt. A merchant (or attacker with a leaked admin token)
    submitting in a loop would burn through NUMU's per-app rate budget
    at Meta and degrade reputation for every other tenant. 30/IP/min
    is generous for legitimate "fix my typo + retry" flow but stops
    sustained spam.

    Per-store (5/store/10min) was specced; needs a per-resource bucket
    that the current sliding-minute window can't express cleanly. The
    per-IP guard catches ~80% of the abuse vector; per-store is a
    polish follow-up.
    """
    return path.startswith("/api/v1/stores/") and path.endswith("/whatsapp/byo/connect")


def _is_whatsapp_dlq_replay(path: str) -> bool:
    """backend-030 / TASK-SEC-004 — DLQ replay enqueues a real send.
    Without a rate limit, a compromised admin token could trigger a
    burst of sends. 20/IP/min keeps replay viable for "bulk re-process
    after fixing the underlying issue" flows while preventing abuse.
    """
    return (
        path.startswith("/api/v1/stores/")
        and "/whatsapp/dead-letters/" in path
        and path.endswith("/replay")
    )


# ------------------------------------------------------------------ #
# Redis sliding-window check
# ------------------------------------------------------------------ #


_IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Parsed form of settings.trusted_proxy_ips, cached against the raw value it
# was built from so a test (or a settings reload) that changes the setting
# doesn't get a stale answer.
_TRUSTED_PROXY_CACHE: tuple[tuple[str, ...], list[_IpNetwork] | None] | None = None


def _trusted_proxy_networks() -> list[_IpNetwork] | None:
    """Parse ``settings.trusted_proxy_ips``; ``None`` means "unconfigured".

    ``None`` is the signal to keep trusting ``X-Forwarded-For`` from anyone —
    see ``_get_client_ip`` for why that stays the default.

    A malformed entry is dropped with an error rather than raised: a typo in
    an ops env var must not refuse to boot the API. If NOTHING in a non-empty
    list parses we return ``None`` (i.e. fall back to the permissive default)
    instead of an empty list, because an empty trusted set means "believe no
    proxy", which in production buckets every request under the load
    balancer's address — one shared bucket platform-wide. Failing back toward
    availability is the right direction for a config typo; the content-keyed
    budgets below are what hold the enumerable surface either way.
    """
    global _TRUSTED_PROXY_CACHE
    configured = tuple(settings.trusted_proxy_ips or ())
    if _TRUSTED_PROXY_CACHE is not None and _TRUSTED_PROXY_CACHE[0] == configured:
        return _TRUSTED_PROXY_CACHE[1]

    networks: list[_IpNetwork] = []
    for entry in configured:
        candidate = (entry or "").strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            logger.error("trusted_proxy_ip_unparseable", entry=candidate)

    parsed = networks or None
    if configured and parsed is None:
        logger.error(
            "trusted_proxy_ips_all_unparseable_falling_back_to_trusting_xff",
            configured=list(configured),
        )
    _TRUSTED_PROXY_CACHE = (configured, parsed)
    return parsed


def _proxy_headers_trusted(peer_ip: str | None) -> bool:
    """Whether the hop that sent us this request may set the client IP."""
    networks = _trusted_proxy_networks()
    if networks is None:
        return True
    if not peer_ip:
        return False
    try:
        addr = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def _get_client_ip(request: Request) -> str:
    """Extract the IP this request is rate-limited under, respecting proxies.

    ``X-Forwarded-For`` is caller-supplied, so it only means anything when the
    hop that set it is one we control. With ``trusted_proxy_ips`` configured we
    require the socket peer to be in it before believing the header; otherwise
    a fresh ``X-Forwarded-For`` per request buys a fresh bucket every time and
    the per-IP limits stop existing.

    While the setting is UNSET we keep believing the header unconditionally,
    exactly as before. Flipping that default blind would collapse every
    production request onto the load balancer's address — one bucket for the
    whole platform — so switching it on is an ops decision that needs the real
    edge topology. Nothing that must not be brute-forced should depend on this
    alone: see ``enforce_track_lookup_budgets`` for the content-keyed buckets
    that hold regardless of how the edge is wired.
    """
    peer = request.client.host if request.client else None

    if _proxy_headers_trusted(peer):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

    return peer or "unknown"


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


def rate_limit_exceeded_response(
    retry_after: int,
    *,
    error: str = "Too many requests. Please slow down.",
    code: str = "RATE_LIMIT_EXCEEDED",
) -> JSONResponse:
    """The single 429 body every limiter in the app answers with.

    Route-level limiters return this rather than raising ``HTTPException``:
    the global exception handler renders ``error`` as an *object*
    (``{"code": …, "message": …}``), so a raised 429 would reach the client in
    a different shape from the middleware's and every caller would need two
    parsers for the same condition.
    """
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "error": error,
            "code": code,
            "details": {"retry_after": retry_after},
        },
        headers={"Retry-After": str(retry_after)},
    )


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


def _stable_digest(value: str) -> str:
    """Process-stable digest for per-identifier bucket keys.

    Built-in ``hash()`` is salted per process (PYTHONHASHSEED), so with N
    workers the same identity would land in a different Redis bucket in
    each worker — multiplying the per-user limit by N and resetting on
    every restart, defeating the point of keeping these counters in
    shared Redis. A truncated SHA-256 keeps one bucket per identity; 64
    bits is ample for bucketing and the raw token is never recoverable
    or logged in full.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


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
        # We don't validate the token here — just digest it to get a
        # stable bucket key. The auth dependency on the route
        # validates as usual; this middleware just needs SOMETHING
        # consistent across requests from the same identity (and across
        # workers — see _stable_digest).
        return f"hbearer:{_stable_digest(auth_header[7:])}"
    cookie_token = request.cookies.get("customer_access_token")
    if cookie_token:
        return f"hcookie:{_stable_digest(cookie_token)}"
    return None


def _is_sensitive_per_user(path: str) -> bool:
    if path in SENSITIVE_PER_USER_PATHS:
        return True
    return any(path.endswith(s) for s in SENSITIVE_CUSTOMER_SUFFIXES)


# ------------------------------------------------------------------ #
# Content-keyed buckets — guest order lookup
# ------------------------------------------------------------------ #
#
# Every bucket above is keyed on an IP, and the IP comes from a header we can
# only conditionally believe (see _get_client_ip). For most endpoints that is
# fine — the rate limit is a fairness measure and auth is the real control.
# Guest order lookup has no auth: order numbers are short and sequential, so
# the rate limit IS the brute-force defence, and it cannot be the kind that a
# caller defeats by varying a header.
#
# These two buckets key on values taken from the request body and path, which
# no header can rotate:
#   * (store, order number) — stops grinding phone/email against one known order
#   * store                 — caps total enumeration throughput against a store
#
# 5/min per order is several times what a customer re-typing their own details
# needs. 60/min per store is above any plausible organic lookup volume for a
# single storefront but far below a useful walk of the number space.
TRACK_LOOKUP_PER_ORDER_PER_MINUTE = 5
TRACK_LOOKUP_PER_STORE_PER_MINUTE = 60


def _normalise_lookup_key(order_number: str) -> str:
    """Collapse the ways one order number can be written into one bucket.

    Mirrors ``_normalise_order_number`` in the tracking route (trim, drop a
    leading ``#``) and additionally casefolds, so decorating the number cannot
    buy a second budget for the same order. Deliberately a local copy rather
    than an import — that route imports this module — and being *stricter*
    than the route is always safe here: the worst case is two spellings that
    couldn't both match a row sharing one budget.
    """
    return order_number.strip().removeprefix("#").strip().casefold()


async def enforce_track_lookup_budgets(
    store_id: UUID | str,
    order_number: str,
) -> JSONResponse | None:
    """Spend the guest-lookup budgets; return the 429 when one is exhausted.

    The caller must invoke this BEFORE reading anything, and both budgets are
    spent on every attempt whether or not the order exists. That ordering is
    the point: a 429 that only appeared for real order numbers would answer
    "does this number exist?", recreating exactly the oracle the uniform 404
    in ``order_tracking.py`` exists to prevent.

    Consequence worth knowing: anyone can burn a specific order's budget by
    replaying its number, locking that order out of the lookup *form* for the
    rest of the minute. The customer's emailed ``/track/{uuid}`` link is
    unaffected, and the alternative — a budget that only counts hits on real
    orders — is the oracle.

    Redis being down fails open, matching ``_check_rate_limit``.
    """
    if not settings.rate_limit_enabled:
        return None

    # Digest rather than interpolate the caller-supplied number: it is
    # free-form text up to 50 chars, and one containing ':' would otherwise
    # let a caller forge extra key segments and land in a bucket that isn't
    # theirs.
    order_key = _stable_digest(_normalise_lookup_key(order_number))

    allowed, count, retry_after = await _check_rate_limit(
        f"lookup:{store_id}:{order_key}",
        "track_lookup_order",
        TRACK_LOOKUP_PER_ORDER_PER_MINUTE,
    )
    if not allowed:
        logger.warning(
            "track_lookup_order_budget_exceeded",
            store_id=str(store_id),
            order_key=order_key,  # digest, never the number itself
            count=count,
            limit=TRACK_LOOKUP_PER_ORDER_PER_MINUTE,
        )
        return rate_limit_exceeded_response(retry_after)

    # Only reached while the per-order budget still had room, so one grinder
    # working a single number can't also drain the store-wide budget and take
    # the form down for every other shopper of that store.
    allowed, count, retry_after = await _check_rate_limit(
        f"lookup:{store_id}",
        "track_lookup_store",
        TRACK_LOOKUP_PER_STORE_PER_MINUTE,
    )
    if not allowed:
        logger.warning(
            "track_lookup_store_budget_exceeded",
            store_id=str(store_id),
            count=count,
            limit=TRACK_LOOKUP_PER_STORE_PER_MINUTE,
        )
        return rate_limit_exceeded_response(retry_after)

    return None


# ------------------------------------------------------------------ #
# Middleware
# ------------------------------------------------------------------ #


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce per-IP rate limits using Redis counters."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        # Built once, when the app assembles its middleware stack — so this is
        # one line per process, not per request.
        if _trusted_proxy_networks() is None:
            logger.warning(
                "rate_limit_trusting_forwarded_header_from_anyone",
                setting="TRUSTED_PROXY_IPS",
                risk=(
                    "X-Forwarded-For is believed from every caller, so any "
                    "client can choose its own per-IP bucket by varying the "
                    "header and the per-IP limits are advisory only. Set "
                    "TRUSTED_PROXY_IPS to the edge in front of this process "
                    "to enforce them — but confirm the real topology first: "
                    "naming the wrong hop buckets the whole platform together."
                ),
            )

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path

        if path in SKIP_RATE_LIMIT:
            return await call_next(request)

        # Determine tier and limit
        if _is_auth_endpoint(path):
            tier = "auth"
            limit = settings.rate_limit_auth_requests_per_minute
        elif _is_checkout(path):
            tier = "checkout"
            limit = settings.rate_limit_checkout_requests_per_minute
        elif _is_coupon_apply(path):
            tier = "coupon"
            limit = 10  # 10 coupon validations per minute per IP
        elif _is_track_lookup(path):
            # Same 10/IP/min as coupon-apply, and for the same reason: both
            # probe a short caller-supplied code, so the bucket has to be
            # tight enough that walking the code space isn't practical. A
            # customer who lost their tracking link needs 1-2 attempts.
            tier = "track_lookup"
            limit = 10
        elif _is_track_beacon(path):
            tier = "tracking"
            limit = 600  # ~10/sec per IP — analytics beacons are noisy
        elif _is_otp(path):
            # 10/IP/min: a real customer needs 1 issue + a couple of verify
            # attempts, maybe one resend. Tight enough that brute-forcing a
            # 6-digit code (1M space, 3 attempts/row anyway) or bulk-issuing
            # codes from one IP is pointless.
            tier = "otp"
            limit = 10
        elif _is_whatsapp_byo_connect(path):
            # backend-030 / TASK-SEC-003 — each BYO connect attempt hits
            # Meta with 3 read calls; a burst would chew through NUMU's
            # per-app rate budget. 30/IP/min permits legitimate retry
            # after a credential typo.
            tier = "whatsapp_byo_connect"
            limit = 30
        elif _is_whatsapp_dlq_replay(path):
            # backend-030 / TASK-SEC-004 — replay enqueues a real send.
            # 20/IP/min keeps bulk-replay-after-fix flows viable while
            # capping abuse from a leaked admin token.
            tier = "whatsapp_dlq_replay"
            limit = 20
        else:
            tier = "general"
            has_auth = "authorization" in request.headers
            if has_auth:
                limit = settings.rate_limit_requests_per_minute
            else:
                limit = settings.rate_limit_anon_requests_per_minute

        client_ip = _get_client_ip(request)

        # Load-test bypass — controlled by a server-side secret. The
        # request must carry `X-Load-Test-Token: <secret>` matching
        # `settings.load_test_bypass_token`. We DELIBERATELY only honour
        # the bypass on the `general` and `tracking` tiers; auth /
        # checkout / coupon-apply remain rate-limited even with a valid
        # token so a leaked token can't be used for credential-stuffing
        # or order-spam. Empty-string token (the default) disables the
        # whole mechanism.
        if (
            tier in ("general", "tracking")
            and settings.load_test_bypass_token
            and request.headers.get("x-load-test-token")
            == settings.load_test_bypass_token
        ):
            logger.info(
                "rate_limit_bypassed",
                ip=client_ip,
                path=path,
                tier=tier,
            )
            return await call_next(request)

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
            return rate_limit_exceeded_response(retry_after)

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
                    return rate_limit_exceeded_response(
                        user_retry,
                        error="Too many requests for this account. Try again later.",
                        code="RATE_LIMIT_EXCEEDED_PER_USER",
                    )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        return response

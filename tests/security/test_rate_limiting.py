"""Tests for the Redis-backed rate limiting middleware.

The original version of this suite tested an in-process ``RateLimiter``
class that no longer exists: rate limiting moved to Redis sliding-window
counters inside ``RateLimitMiddleware`` (src/api/middleware/rate_limit.py).
This rewrite preserves the original suite's intents against the real
middleware:

- requests within the limit pass; the next one is rejected with 429
- auth endpoints get a stricter, separate bucket than general traffic
- per-IP separation (one abusive IP cannot exhaust another IP's budget)
- proxy-header IP extraction (X-Forwarded-For first hop, X-Real-IP)
- skip-list endpoints (health/docs) are never rate limited
- Retry-After calculation and the 429 payload contract
- counter expiry (the legacy in-process "cleanup()" is now a per-minute
  window key plus a Redis TTL)

and covers the middleware surface that did not exist when the original
suite was written: tier path matchers (checkout/coupon/track/WhatsApp),
the load-test bypass token (and the tiers it deliberately does NOT
cover), and the per-identifier credential-stuffing bucket on sensitive
auth paths.

No live Redis is required: the module-level lazy ``_cache`` is
monkeypatched with an in-memory fake implementing exactly the surface the
middleware uses (``await cache._get_client()`` -> ``client.incr(key)`` /
``client.expire(key, ttl)``). Time is frozen by replacing the middleware
module's ``time`` import so window math is deterministic.
"""

import hashlib

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

import src.api.middleware.rate_limit as rate_limit_module
from src.api.middleware.rate_limit import (
    AUTH_ENDPOINTS,
    CUSTOMER_AUTH_SUFFIXES,
    SENSITIVE_CUSTOMER_SUFFIXES,
    SENSITIVE_PER_USER_PATHS,
    SKIP_RATE_LIMIT,
    RateLimitMiddleware,
    _get_client_ip,
    _is_auth_endpoint,
    _is_checkout,
    _is_coupon_apply,
    _is_track_beacon,
    _is_whatsapp_byo_connect,
    _is_whatsapp_dlq_replay,
)
from src.config import settings
from src.config.settings import Settings

# Epoch chosen as an exact multiple of 60 so a blocked request at the
# frozen instant has retry_after == 60 - (t % 60) == 60 exactly.
WINDOW_START = 1_699_999_980.0

STORE_PREFIX = "/api/v1/storefront/store/11111111-1111-1111-1111-111111111111"


# ------------------------------------------------------------------ #
# Test doubles
# ------------------------------------------------------------------ #


class FakeRedisClient:
    """In-memory stand-in for the redis asyncio client.

    Implements the only two calls ``_check_rate_limit`` /
    ``_check_per_user_limit`` make: INCR and EXPIRE.
    """

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True


class FakeRedisCache:
    """Stands in for RedisCacheService; only ``_get_client`` is used."""

    def __init__(self) -> None:
        self.client = FakeRedisClient()

    async def _get_client(self) -> FakeRedisClient:
        return self.client


class DownRedisCache:
    """Simulates Redis being unreachable."""

    async def _get_client(self) -> FakeRedisClient:
        raise ConnectionError("redis unavailable")


class FrozenTime:
    """Replaces the middleware module's ``time`` import (module object),
    making ``time.time()`` deterministic inside rate_limit.py only."""

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedisClient:
    """Route the middleware's Redis access to an in-memory fake and turn
    rate limiting on (the test env disables it globally via
    RATE_LIMIT_ENABLED=false in tests/conftest.py)."""
    cache = FakeRedisCache()
    monkeypatch.setattr(rate_limit_module, "_cache", cache)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    return cache.client


@pytest.fixture
def frozen_time(monkeypatch: pytest.MonkeyPatch) -> FrozenTime:
    frozen = FrozenTime(WINDOW_START)
    monkeypatch.setattr(rate_limit_module, "time", frozen)
    return frozen


def _build_app() -> FastAPI:
    """Minimal app: the real middleware in front of a catch-all route, so
    any path (auth, checkout, health, ...) resolves without defining the
    full API surface."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.api_route("/{rest:path}", methods=["GET", "POST"])
    async def _echo(rest: str) -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest_asyncio.fixture
async def http():
    async with AsyncClient(
        transport=ASGITransport(app=_build_app()), base_url="http://testserver"
    ) as client:
        yield client


def _make_request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("127.0.0.1", 1234),
) -> Request:
    """Build a real starlette Request (not a MagicMock) so header lookups
    exercise real case-insensitive Headers behaviour."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/anything",
        "query_string": b"",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "client": client,
    }
    return Request(scope)


def _rotating_ip(i: int) -> dict[str, str]:
    """A unique X-Forwarded-For per call — the credential-stuffing shape."""
    return {"X-Forwarded-For": f"10.7.{i // 250}.{i % 250}"}


# ------------------------------------------------------------------ #
# Client IP extraction (original intent: proxy headers drive identity)
# ------------------------------------------------------------------ #


class TestClientIpExtraction:
    """Tests for _get_client_ip proxy-header handling."""

    def test_x_forwarded_for_first_hop_wins(self):
        request = _make_request({"X-Forwarded-For": "10.0.0.1, 10.0.0.2"})
        assert _get_client_ip(request) == "10.0.0.1"

    def test_x_real_ip_used_when_no_forwarded_for(self):
        request = _make_request({"X-Real-IP": "10.0.0.5"})
        assert _get_client_ip(request) == "10.0.0.5"

    def test_direct_client_host_fallback(self):
        request = _make_request(client=("192.168.7.9", 5555))
        assert _get_client_ip(request) == "192.168.7.9"

    def test_unknown_when_no_client(self):
        request = _make_request(client=None)
        assert _get_client_ip(request) == "unknown"


# ------------------------------------------------------------------ #
# Endpoint configuration (kept from the original suite, extended)
# ------------------------------------------------------------------ #


class TestEndpointConfiguration:
    """Tests for the middleware's endpoint sets and tier matchers."""

    def test_health_endpoints_skipped(self):
        assert "/" in SKIP_RATE_LIMIT
        assert "/health" in SKIP_RATE_LIMIT
        assert "/api/v1/health" in SKIP_RATE_LIMIT
        assert "/api/v1/public/health" in SKIP_RATE_LIMIT

    def test_docs_endpoints_skipped(self):
        assert "/docs" in SKIP_RATE_LIMIT
        assert "/redoc" in SKIP_RATE_LIMIT
        assert "/openapi.json" in SKIP_RATE_LIMIT

    def test_auth_endpoints_have_stricter_limits(self):
        """Auth endpoints are in the strict tier, and the shipped default
        limits really are tiered strictest-first (asserted on the Settings
        field defaults so a local .env override can't mask a regression).

        RL-1 fix (2026-07-21): the set now carries only real mounted
        routes — merchant + admin; the dead /public/auth/* and
        /storefront/{auth,customers}/* strings were pruned and store-scoped
        customer auth moved to the _is_auth_endpoint matcher."""
        assert "/api/v1/auth/login" in AUTH_ENDPOINTS
        assert "/api/v1/auth/register" in AUTH_ENDPOINTS
        assert "/api/v1/auth/refresh" in AUTH_ENDPOINTS
        assert "/api/v1/admin/auth/login" in AUTH_ENDPOINTS
        assert "/api/v1/admin/auth/refresh" in AUTH_ENDPOINTS

        fields = Settings.model_fields
        auth = fields["rate_limit_auth_requests_per_minute"].default
        checkout = fields["rate_limit_checkout_requests_per_minute"].default
        anon = fields["rate_limit_anon_requests_per_minute"].default
        authed = fields["rate_limit_requests_per_minute"].default
        assert auth < checkout <= anon <= authed

    def test_auth_endpoint_entries_all_match_mounted_routes(self):
        """Every exact-string entry in AUTH_ENDPOINTS must correspond to a
        route actually mounted on the app. Guards against the RL-1 rot
        class: 7 of 10 entries had drifted to paths that matched nothing,
        silently dropping real logins to the general tier."""
        from src.main import app

        route_paths = {
            path
            for path in (getattr(route, "path", None) for route in app.routes)
            if path is not None
        }
        dead_entries = AUTH_ENDPOINTS - route_paths
        assert dead_entries == set(), (
            f"AUTH_ENDPOINTS entries matching no mounted route: {dead_entries}"
        )

    def test_is_auth_endpoint_matcher(self):
        """The strict-tier matcher: exact merchant/admin paths plus the
        dynamic store-scoped customer auth routes (which an exact-string
        set can never match — the RL-1 defect)."""
        # Exact entries still work
        assert _is_auth_endpoint("/api/v1/auth/login")
        assert _is_auth_endpoint("/api/v1/admin/auth/login")
        assert _is_auth_endpoint("/api/v1/admin/auth/refresh")

        # Store-scoped customer auth (dynamic store id)
        assert _is_auth_endpoint(f"{STORE_PREFIX}/auth/login")
        assert _is_auth_endpoint(f"{STORE_PREFIX}/auth/register")
        assert _is_auth_endpoint(f"{STORE_PREFIX}/auth/refresh")
        assert set(CUSTOMER_AUTH_SUFFIXES) == {
            "/auth/login",
            "/auth/register",
            "/auth/refresh",
        }

        # Near-misses stay on their own tiers
        assert not _is_auth_endpoint(f"{STORE_PREFIX}/auth/forgot-password")
        assert not _is_auth_endpoint(f"{STORE_PREFIX}/products")
        assert not _is_auth_endpoint("/api/v1/auth/logout")
        assert not _is_auth_endpoint("/api/v1/stores/abc/auth/login")

    def test_sensitive_per_user_paths_cover_login_flows(self):
        assert "/api/v1/auth/login" in SENSITIVE_PER_USER_PATHS
        assert "/api/v1/auth/forgot-password" in SENSITIVE_PER_USER_PATHS
        assert "/api/v1/auth/reset-password" in SENSITIVE_PER_USER_PATHS
        assert "/auth/login" in SENSITIVE_CUSTOMER_SUFFIXES
        assert "/auth/register" in SENSITIVE_CUSTOMER_SUFFIXES

    def test_tier_path_matchers(self):
        assert _is_checkout(f"{STORE_PREFIX}/checkout")
        assert not _is_checkout(f"{STORE_PREFIX}/checkout/session")
        assert not _is_checkout("/api/v1/stores/abc/checkout")  # merchant side

        assert _is_coupon_apply(f"{STORE_PREFIX}/coupons/apply")
        assert not _is_coupon_apply(f"{STORE_PREFIX}/coupons")

        assert _is_track_beacon(f"{STORE_PREFIX}/track")
        assert not _is_track_beacon(f"{STORE_PREFIX}/tracking")

        assert _is_whatsapp_byo_connect("/api/v1/stores/abc/whatsapp/byo/connect")
        assert not _is_whatsapp_byo_connect(f"{STORE_PREFIX}/whatsapp/byo/connect")

        assert _is_whatsapp_dlq_replay(
            "/api/v1/stores/abc/whatsapp/dead-letters/42/replay"
        )
        assert not _is_whatsapp_dlq_replay("/api/v1/stores/abc/whatsapp/dead-letters")


# ------------------------------------------------------------------ #
# Middleware behaviour (through a real ASGI app)
# ------------------------------------------------------------------ #


class TestRateLimitMiddleware:
    """End-to-end behaviour of RateLimitMiddleware with fake Redis."""

    async def test_disabled_setting_bypasses_middleware(
        self, http: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """rate_limit_enabled=False is a kill switch: nothing is counted
        and no rate-limit headers are added."""
        cache = FakeRedisCache()
        monkeypatch.setattr(rate_limit_module, "_cache", cache)
        monkeypatch.setattr(settings, "rate_limit_enabled", False)

        response = await http.get("/api/v1/products")

        assert response.status_code == 200
        assert "X-RateLimit-Limit" not in response.headers
        assert cache.client.counters == {}

    async def test_requests_within_limit_allowed_with_headers(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Original intent: first request and every request within the
        limit pass; responses expose limit/remaining headers."""
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 5)

        for i in range(5):
            response = await http.get("/api/v1/products")
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Limit"] == "5"
            assert response.headers["X-RateLimit-Remaining"] == str(5 - (i + 1))

    async def test_request_over_limit_gets_429_contract(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Original intent: exceeding the limit blocks, with a correct
        Retry-After. Frozen at the exact window start, retry_after is
        deterministically 60."""
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 3)

        for _ in range(3):
            assert (await http.get("/api/v1/products")).status_code == 200

        response = await http.get("/api/v1/products")

        assert response.status_code == 429
        body = response.json()
        assert body["success"] is False
        assert body["code"] == "RATE_LIMIT_EXCEEDED"
        assert body["details"]["retry_after"] == 60
        assert response.headers["Retry-After"] == "60"

    async def test_auth_tier_stricter_and_isolated_from_general(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Auth endpoints hit their own (stricter) bucket; exhausting it
        does not consume the same IP's general-tier budget."""
        monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 3)
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 10)

        for _ in range(3):
            assert (await http.post("/api/v1/auth/login")).status_code == 200
        assert (await http.post("/api/v1/auth/login")).status_code == 429

        assert (await http.get("/api/v1/products")).status_code == 200

    async def test_auth_endpoints_share_one_bucket(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Deliberate change from the legacy per-path limiter: all auth
        endpoints share one per-IP bucket, so an attacker cannot multiply
        their budget by rotating across login/register/refresh."""
        monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 3)

        for _ in range(3):
            assert (await http.post("/api/v1/auth/login")).status_code == 200

        assert (await http.post("/api/v1/auth/register")).status_code == 429

    async def test_customer_login_rides_auth_tier(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """RL-1 regression: the store-scoped customer login (dynamic store
        id) used to fall through to the general tier (60/min anon) because
        AUTH_ENDPOINTS matched by exact string only. It must get the
        strict auth limit — proven by the limit header carrying the auth
        limit, not the (higher) anon limit, and the block landing at it."""
        monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 2)
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 10)
        path = f"{STORE_PREFIX}/auth/login"

        for _ in range(2):
            response = await http.post(path)
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Limit"] == "2"

        assert (await http.post(path)).status_code == 429

    async def test_admin_login_rides_auth_tier(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """RL-1 regression: the platform-admin login was absent from
        AUTH_ENDPOINTS and rode the general tier. It must get the strict
        auth limit."""
        monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 2)
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 10)
        path = "/api/v1/admin/auth/login"

        for _ in range(2):
            response = await http.post(path)
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Limit"] == "2"

        assert (await http.post(path)).status_code == 429

    async def test_different_ips_have_separate_limits(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Original intent: buckets are keyed per client IP."""
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 2)
        ip_a = {"X-Forwarded-For": "203.0.113.1"}
        ip_b = {"X-Forwarded-For": "203.0.113.2"}

        for _ in range(2):
            assert (await http.get("/api/v1/products", headers=ip_a)).status_code == 200
        assert (await http.get("/api/v1/products", headers=ip_a)).status_code == 429

        assert (await http.get("/api/v1/products", headers=ip_b)).status_code == 200

    async def test_skip_list_paths_bypass_even_when_ip_is_exhausted(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Original intent: health endpoints are never rate limited —
        proven behaviourally by exhausting the IP first."""
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 1)

        assert (await http.get("/api/v1/products")).status_code == 200
        assert (await http.get("/api/v1/products")).status_code == 429

        for path in ("/", "/health", "/api/v1/health", "/api/v1/public/health"):
            response = await http.get(path)
            assert response.status_code == 200, path
            assert "X-RateLimit-Limit" not in response.headers, path

    async def test_checkout_tier_uses_checkout_limit(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Storefront checkout paths resolve to the checkout tier and its
        dedicated limit."""
        monkeypatch.setattr(settings, "rate_limit_checkout_requests_per_minute", 2)
        path = f"{STORE_PREFIX}/checkout"

        for _ in range(2):
            response = await http.post(path)
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Limit"] == "2"

        assert (await http.post(path)).status_code == 429

    async def test_authenticated_general_limit_higher_than_anon(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """General tier: requests with an Authorization header get the
        higher authenticated limit; anonymous ones the lower limit.
        Separate IPs because both variants share the per-IP bucket."""
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 2)
        monkeypatch.setattr(settings, "rate_limit_requests_per_minute", 4)
        anon = {"X-Forwarded-For": "203.0.113.10"}
        authed = {
            "X-Forwarded-For": "203.0.113.11",
            "Authorization": "Bearer some-token",
        }

        for _ in range(2):
            assert (await http.get("/api/v1/orders", headers=anon)).status_code == 200
        assert (await http.get("/api/v1/orders", headers=anon)).status_code == 429

        for _ in range(4):
            response = await http.get("/api/v1/orders", headers=authed)
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Limit"] == "4"
        assert (await http.get("/api/v1/orders", headers=authed)).status_code == 429

    async def test_window_rollover_resets_the_counter(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Original 'cleanup removes old entries' intent, translated to
        the Redis design: counters live in per-minute window keys with a
        90s TTL, so the next minute starts a fresh bucket."""
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 2)

        for _ in range(2):
            assert (await http.get("/api/v1/products")).status_code == 200
        assert (await http.get("/api/v1/products")).status_code == 429

        frozen_time.advance(60)

        assert (await http.get("/api/v1/products")).status_code == 200

        # Two window keys were created (old + new); each got its TTL on
        # first INCR, and the blocked request still incremented the old key.
        assert sorted(fake_redis.counters.values()) == [1, 3]
        assert list(fake_redis.ttls.values()) == [90, 90]

    async def test_redis_down_fails_open(
        self,
        http: AsyncClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Redis being unreachable must degrade to allowing traffic —
        availability wins over limiting (documented middleware policy)."""
        monkeypatch.setattr(rate_limit_module, "_cache", DownRedisCache())
        monkeypatch.setattr(settings, "rate_limit_enabled", True)
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 1)

        for _ in range(4):
            assert (await http.get("/api/v1/products")).status_code == 200


# ------------------------------------------------------------------ #
# Load-test bypass token
# ------------------------------------------------------------------ #


class TestLoadTestBypass:
    """The X-Load-Test-Token bypass and its deliberate limits."""

    async def test_bypass_token_skips_counting_on_general_tier(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(settings, "load_test_bypass_token", "s3cret-load-test")
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 2)
        headers = {
            "X-Load-Test-Token": "s3cret-load-test",
            "X-Forwarded-For": "198.51.100.7",
        }

        for _ in range(6):
            assert (
                await http.get("/api/v1/products", headers=headers)
            ).status_code == 200

        # Bypass short-circuits BEFORE counting — no counters for that IP.
        assert not any("198.51.100.7" in key for key in fake_redis.counters)

    async def test_bypass_token_never_covers_auth_tier(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Security property: a leaked load-test token must not enable
        credential-stuffing — auth stays limited even with a valid token."""
        monkeypatch.setattr(settings, "load_test_bypass_token", "s3cret-load-test")
        monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 2)
        headers = {"X-Load-Test-Token": "s3cret-load-test"}

        for _ in range(2):
            assert (
                await http.post("/api/v1/auth/login", headers=headers)
            ).status_code == 200

        assert (
            await http.post("/api/v1/auth/login", headers=headers)
        ).status_code == 429

    async def test_wrong_token_gets_no_bypass(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(settings, "load_test_bypass_token", "s3cret-load-test")
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 2)
        headers = {"X-Load-Test-Token": "wrong-token"}

        for _ in range(2):
            assert (
                await http.get("/api/v1/products", headers=headers)
            ).status_code == 200

        assert (await http.get("/api/v1/products", headers=headers)).status_code == 429

    async def test_empty_configured_token_disables_the_mechanism(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The default empty-string token means no header value — not even
        an empty one — can bypass."""
        monkeypatch.setattr(settings, "load_test_bypass_token", "")
        monkeypatch.setattr(settings, "rate_limit_anon_requests_per_minute", 2)
        headers = {"X-Load-Test-Token": ""}

        for _ in range(2):
            assert (
                await http.get("/api/v1/products", headers=headers)
            ).status_code == 200

        assert (await http.get("/api/v1/products", headers=headers)).status_code == 429


# ------------------------------------------------------------------ #
# Per-identifier credential-stuffing bucket (Phase 5.2)
# ------------------------------------------------------------------ #


class TestPerUserCredentialStuffingBucket:
    """The hourly 30/identifier bucket on sensitive auth paths."""

    async def test_rotating_ips_with_same_bearer_hits_per_user_bucket(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The exact threat this bucket exists for: rotate the IP on every
        request (per-IP count stays at 1) while keeping one identity —
        request 31 must trip the per-user limit."""
        monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 5)
        bearer = {"Authorization": "Bearer stuffing-run-token"}

        for i in range(30):
            response = await http.post(
                "/api/v1/auth/login", headers={**bearer, **_rotating_ip(i)}
            )
            assert response.status_code == 200, f"request {i} unexpectedly blocked"

        response = await http.post(
            "/api/v1/auth/login", headers={**bearer, **_rotating_ip(30)}
        )

        assert response.status_code == 429
        body = response.json()
        assert body["code"] == "RATE_LIMIT_EXCEEDED_PER_USER"
        expected_retry = 3600 - (int(WINDOW_START) % 3600)
        assert body["details"]["retry_after"] == expected_retry
        assert response.headers["Retry-After"] == str(expected_retry)

        # RL-2 regression: the bucket key must embed a digest computable
        # OUTSIDE this process (sha256 prefix of the raw token), i.e. one
        # shared bucket across workers — not builtin hash(), which
        # PYTHONHASHSEED salts per process and would multiply the limit
        # by the worker count.
        expected_digest = hashlib.sha256(b"stuffing-run-token").hexdigest()[:16]
        assert any(
            f"hbearer:{expected_digest}" in key for key in fake_redis.counters
        ), "per-user key does not use the process-stable sha256 digest"

    async def test_customer_cookie_identity_on_sensitive_suffix_path(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Storefront customer auth paths (matched by suffix, store id is
        dynamic) bucket on the customer_access_token cookie. Since the
        RL-1 fix this path rides the AUTH tier per IP, so that limit is
        the one that must stay out of the way while IPs rotate."""
        monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 5)
        http.cookies.set("customer_access_token", "customer-token-1")
        path = f"{STORE_PREFIX}/auth/login"

        for i in range(30):
            response = await http.post(path, headers=_rotating_ip(i))
            assert response.status_code == 200, f"request {i} unexpectedly blocked"

        response = await http.post(path, headers=_rotating_ip(30))

        assert response.status_code == 429
        assert response.json()["code"] == "RATE_LIMIT_EXCEEDED_PER_USER"

    async def test_anonymous_requests_are_not_per_user_bucketed(
        self,
        http: AsyncClient,
        fake_redis: FakeRedisClient,
        frozen_time: FrozenTime,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """No bearer token and no customer cookie means no identifier can
        be derived, so only the per-IP check applies (documented
        behaviour) — rotating IPs sail through the per-user layer."""
        monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 5)

        for i in range(35):
            response = await http.post("/api/v1/auth/login", headers=_rotating_ip(i))
            assert response.status_code == 200, f"request {i} unexpectedly blocked"

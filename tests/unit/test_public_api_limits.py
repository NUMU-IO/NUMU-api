"""Merchant API keys: entitlement, scopes, rate limits, quota, rotation.

The limiter tests run the real Lua script, so they need Redis (the local
docker one); they skip without it. Time is passed in, never read, so every
window a test fills is the same window on every run.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.requests import Request

from src.api.dependencies import auth
from src.application.services import api_limits
from src.application.services.api_limits import (
    BULK,
    HEAVY,
    LIGHT_READ,
    QUOTA_EXCEEDED,
    RATE_LIMITED,
    STANDARD_WRITE,
    ApiPolicy,
    category_for,
    category_limit,
    check_and_count,
)
from src.application.services.personal_access_token_service import (
    PersonalAccessTokenService,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel

NOW = datetime(2026, 9, 25, 10, 30, 5, tzinfo=UTC)


# ------------------------------------------------------------------ #
# Categories
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("path", "method", "expected"),
    [
        ("/api/v1/stores/s/orders", "GET", LIGHT_READ),
        ("/api/v1/stores/s/products/p", "PATCH", STANDARD_WRITE),
        ("/api/v1/stores/s/orders", "POST", STANDARD_WRITE),
        ("/api/v1/stores/s/analytics/overview", "GET", HEAVY),
        ("/api/v1/stores/s/orders/export", "GET", HEAVY),
        ("/api/v1/stores/s/products/bulk-update", "POST", BULK),
        ("/api/v1/stores/s/products/import", "POST", BULK),
    ],
)
def test_requests_are_bucketed_by_cost(path, method, expected):
    assert category_for(path, method) == expected


def test_category_limits_derive_from_the_merchant_rate():
    assert category_limit(LIGHT_READ, 60) == 60
    assert category_limit(STANDARD_WRITE, 60) == 30
    assert category_limit(HEAVY, 60) == 10
    assert category_limit(BULK, 60) == 2
    assert category_limit(HEAVY, 5) == 5
    assert category_limit(BULK, None) is None


# ------------------------------------------------------------------ #
# Policy from entitlements
# ------------------------------------------------------------------ #


async def _tenant(session, plan: str) -> TenantModel:
    tenant = TenantModel(
        id=uuid4(),
        name="API test",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan=plan,
        lifecycle_state="active",
        owner_id=uuid4(),
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def test_pro_gets_the_default_limits(test_session):
    policy = await api_limits.compute_policy(
        test_session, await _tenant(test_session, "pro")
    )
    assert policy == ApiPolicy(True, 60, 10, 200000, 5)


async def test_enterprise_gets_its_plan_rows(test_session):
    policy = await api_limits.compute_policy(
        test_session, await _tenant(test_session, "enterprise")
    )
    assert policy == ApiPolicy(True, 300, 30, 2000000, 25)


async def test_starter_has_no_api_access(test_session):
    policy = await api_limits.compute_policy(
        test_session, await _tenant(test_session, "starter")
    )
    assert policy.allowed is False


# ------------------------------------------------------------------ #
# Limiter (real Redis)
# ------------------------------------------------------------------ #


@pytest.fixture
async def redis_client():
    from src.infrastructure.cache.redis_cache import RedisCacheService

    cache = RedisCacheService()
    try:
        client = await cache._get_client()
        await client.ping()
    except Exception:
        pytest.skip("Redis not reachable")
    previous = api_limits._redis
    api_limits._redis = cache
    yield client
    api_limits._redis = previous


def _no_history():
    @asynccontextmanager
    async def factory():
        yield None

    return factory


@pytest.fixture(autouse=True)
def _zero_history(monkeypatch):
    async def month_usage(session, tenant_id, now):
        return 0

    monkeypatch.setattr(api_limits, "month_usage_from_db", month_usage)


async def _hit(
    tenant, token, policy, *, now=NOW, path="/api/v1/stores/s/orders", method="GET"
):
    return await check_and_count(
        tenant_id=tenant,
        token_id=token,
        path=path,
        method=method,
        policy=policy,
        now=now,
        session_factory=_no_history(),
    )


async def test_per_minute_limit(redis_client):
    tenant, token = str(uuid4()), str(uuid4())
    policy = ApiPolicy(True, 5, None, None, None)
    for i in range(5):
        decision = await _hit(tenant, token, policy, now=NOW + timedelta(seconds=i))
        assert decision.allowed and decision.remaining == 4 - i
    blocked = await _hit(tenant, token, policy, now=NOW + timedelta(seconds=10))
    assert (blocked.allowed, blocked.code) == (False, RATE_LIMITED)
    assert blocked.retry_after == 60 - 15  # NOW is :05, blocked at :15
    assert blocked.headers()["Retry-After"] == "45"
    # The next minute is a new window.
    assert (await _hit(tenant, token, policy, now=NOW + timedelta(seconds=60))).allowed


async def test_burst_limit_per_second(redis_client):
    tenant, token = str(uuid4()), str(uuid4())
    policy = ApiPolicy(True, 100, 3, None, None)
    for _ in range(3):
        assert (await _hit(tenant, token, policy)).allowed
    blocked = await _hit(tenant, token, policy)
    assert (blocked.allowed, blocked.retry_after) == (False, 1)
    assert (await _hit(tenant, token, policy, now=NOW + timedelta(seconds=1))).allowed


async def test_more_keys_do_not_raise_the_merchant_limit(redis_client):
    tenant = str(uuid4())
    policy = ApiPolicy(True, 4, None, None, None)
    keys = [str(uuid4()) for _ in range(4)]
    for key in keys:
        assert (await _hit(tenant, key, policy)).allowed
    fresh_key = str(uuid4())
    assert (await _hit(tenant, fresh_key, policy)).code == RATE_LIMITED


async def test_a_blocked_request_is_not_counted(redis_client):
    tenant, token = str(uuid4()), str(uuid4())
    policy = ApiPolicy(True, 1, None, 10, None)
    assert (await _hit(tenant, token, policy)).allowed
    for _ in range(3):
        assert not (await _hit(tenant, token, policy)).allowed
    assert await api_limits.quota_used(tenant, NOW) == 1


async def test_heavy_endpoints_have_their_own_smaller_window(redis_client):
    tenant, token = str(uuid4()), str(uuid4())
    policy = ApiPolicy(True, 60, None, None, None)
    report = "/api/v1/stores/s/analytics/overview"
    for _ in range(10):
        assert (await _hit(tenant, token, policy, path=report)).allowed
    assert (await _hit(tenant, token, policy, path=report)).code == RATE_LIMITED
    assert (await _hit(tenant, token, policy)).allowed  # reads still flow


async def test_monthly_quota(redis_client):
    tenant, token = str(uuid4()), str(uuid4())
    policy = ApiPolicy(True, None, None, 3, None)
    for i in range(3):
        decision = await _hit(tenant, token, policy)
        assert decision.quota_remaining == 2 - i
    blocked = await _hit(tenant, token, policy)
    assert (blocked.allowed, blocked.code) == (False, QUOTA_EXCEEDED)
    assert blocked.retry_after == int(
        (datetime(2026, 10, 1, tzinfo=UTC) - NOW).total_seconds()
    )


async def test_quota_counter_is_rebuilt_from_history(redis_client, monkeypatch):
    tenant, token = str(uuid4()), str(uuid4())

    async def month_usage(session, tenant_id, now):
        return 9

    monkeypatch.setattr(api_limits, "month_usage_from_db", month_usage)
    policy = ApiPolicy(True, None, None, 10, None)
    assert (await _hit(tenant, token, policy)).quota_remaining == 0
    assert (await _hit(tenant, token, policy)).code == QUOTA_EXCEEDED


async def test_unlimited_never_blocks(redis_client):
    tenant, token = str(uuid4()), str(uuid4())
    policy = ApiPolicy(True, None, None, None, None)
    for _ in range(50):
        assert (await _hit(tenant, token, policy)).allowed


async def test_redis_down_fails_open(monkeypatch):
    class Broken:
        async def _get_client(self):
            raise ConnectionError("down")

    monkeypatch.setattr(api_limits, "_redis", Broken())
    decision = await _hit(str(uuid4()), str(uuid4()), ApiPolicy(True, 1, 1, 1, None))
    assert decision.allowed and decision.headers() == {}


def test_usage_fields_round_trip():
    class Pipe:
        def __init__(self):
            self.h = {}

        def hincrby(self, key, field, n):
            self.h[field] = self.h.get(field, 0) + n

        def expire(self, *a):
            pass

        def sadd(self, *a):
            pass

    pipe = Pipe()
    for status in (200, 404, 429, 503):
        api_limits.queue_usage(
            pipe,
            tenant_id="t",
            token_id="k",
            method="get",
            route="/api/v1/stores/{store_id}/orders",
            status=status,
            ms=10.4,
            now=NOW,
        )
    rows = api_limits.parse_usage({f: str(v) for f, v in pipe.h.items()})
    assert rows == {
        ("k", "GET", "/api/v1/stores/{store_id}/orders"): {
            "requests": 4,
            "latency_ms_sum": 40,
            "errors_4xx": 1,
            "errors_5xx": 1,
            "throttled": 1,
        }
    }


# ------------------------------------------------------------------ #
# The auth pipeline
# ------------------------------------------------------------------ #


@pytest.fixture
def pat_env(test_engine, monkeypatch):
    """Point the resolver's own session at the test DB; no Redis caches."""
    import src.infrastructure.database.connection as connection

    monkeypatch.setattr(
        connection,
        "AsyncSessionLocal",
        async_sessionmaker(test_engine, expire_on_commit=False),
    )

    class NoCache:
        async def get(self, key):
            return None

        async def set(self, *a, **k):
            return True

        async def delete(self, key):
            return True

        async def _get_client(self):
            raise ConnectionError("no redis in this test")

    monkeypatch.setattr(api_limits, "_redis", NoCache())


async def _mint(session, plan="pro", scopes=("orders:read",), expires_at=None):
    tenant = await _tenant(session, plan)
    user = UserModel(
        id=uuid4(),
        email=f"{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        first_name="A",
        last_name="B",
    )
    session.add(user)
    await session.flush()
    store_id = uuid4()
    raw, record = await PersonalAccessTokenService(session).create(
        user_id=user.id,
        tenant_id=tenant.id,
        store_id=store_id,
        name="ERP",
        scopes=list(scopes) if scopes else None,
        expires_at=expires_at,
    )
    await session.commit()
    return raw, record, store_id


def _request(path: str, method: str = "GET") -> Request:
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
    })


async def test_a_valid_key_resolves_to_its_merchant(test_session, pat_env):
    raw, record, store_id = await _mint(test_session)
    request = _request(f"/api/v1/stores/{store_id}/orders")
    payload = await auth._resolve_pat_principal(raw, request)
    assert payload.tenant_id == record.tenant_id
    assert request.state.pat["token_id"] == str(record.id)
    # Resolving again for the same request is free and counts nothing twice.
    assert await auth._resolve_pat_principal(raw, request) is payload


async def test_an_unknown_key_is_401(test_session, pat_env):
    with pytest.raises(HTTPException) as exc:
        await auth._resolve_pat_principal(
            "numu_pat_" + "x" * 43, _request("/api/v1/stores/s/orders")
        )
    assert exc.value.status_code == 401


async def test_a_revoked_key_is_401(test_session, pat_env):
    raw, record, store_id = await _mint(test_session)
    await PersonalAccessTokenService(test_session).revoke(
        token_id=record.id, user_id=record.user_id
    )
    await test_session.commit()
    with pytest.raises(HTTPException) as exc:
        await auth._resolve_pat_principal(
            raw, _request(f"/api/v1/stores/{store_id}/orders")
        )
    assert exc.value.status_code == 401


async def test_an_expired_key_is_401(test_session, pat_env):
    raw, _, store_id = await _mint(
        test_session, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    with pytest.raises(HTTPException) as exc:
        await auth._resolve_pat_principal(
            raw, _request(f"/api/v1/stores/{store_id}/orders")
        )
    assert exc.value.status_code == 401


async def test_a_missing_scope_is_403(test_session, pat_env):
    raw, _, store_id = await _mint(test_session, scopes=("orders:read",))
    with pytest.raises(HTTPException) as exc:
        await auth._resolve_pat_principal(
            raw, _request(f"/api/v1/stores/{store_id}/orders", "POST")
        )
    assert exc.value.status_code == 403
    assert "orders:write" in exc.value.detail


async def test_a_merchant_without_the_entitlement_is_403(test_session, pat_env):
    raw, _, store_id = await _mint(test_session, plan="starter")
    with pytest.raises(HTTPException) as exc:
        await auth._resolve_pat_principal(
            raw, _request(f"/api/v1/stores/{store_id}/orders")
        )
    assert exc.value.status_code == 403
    assert "not enabled" in exc.value.detail


async def test_another_stores_routes_are_refused(test_session, pat_env):
    raw, _, _ = await _mint(test_session)
    with pytest.raises(HTTPException) as exc:
        await auth._resolve_pat_principal(
            raw, _request(f"/api/v1/stores/{uuid4()}/orders")
        )
    assert exc.value.status_code == 403


async def test_throttling_is_a_429_with_retry_after(test_session, pat_env, monkeypatch):
    raw, _, store_id = await _mint(test_session)

    async def refuse(**kwargs):
        return api_limits.LimitDecision(
            allowed=False,
            code=RATE_LIMITED,
            limit=60,
            remaining=0,
            reset=1,
            retry_after=18,
        )

    monkeypatch.setattr(api_limits, "check_and_count", refuse)
    with pytest.raises(HTTPException) as exc:
        await auth._resolve_pat_principal(
            raw, _request(f"/api/v1/stores/{store_id}/orders")
        )
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "rate_limit_exceeded"
    assert exc.value.detail["retry_after"] == 18
    assert exc.value.headers["Retry-After"] == "18"


# ------------------------------------------------------------------ #
# Key management routes
# ------------------------------------------------------------------ #


async def test_rotation_replaces_the_secret(test_session, pat_env):
    from src.api.v1.routes.stores.access_tokens import rotate_access_token

    raw, record, store_id = await _mint(
        test_session, expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    store = SimpleNamespace(
        id=store_id, tenant_id=record.tenant_id, owner_id=record.user_id
    )
    rotated = (
        await rotate_access_token(store=store, db=test_session, token_id=record.id)
    ).data
    assert (
        rotated.token != raw
        and rotated.name == "ERP"
        and rotated.scopes == ["orders:read"]
    )
    with pytest.raises(HTTPException):
        await auth._resolve_pat_principal(
            raw, _request(f"/api/v1/stores/{store_id}/orders")
        )
    payload = await auth._resolve_pat_principal(
        rotated.token, _request(f"/api/v1/stores/{store_id}/orders")
    )
    assert payload.tenant_id == record.tenant_id


async def test_the_key_limit_counts_live_keys(test_session, pat_env, monkeypatch):
    from src.api.v1.routes.stores.access_tokens import (
        CreateAccessTokenRequest,
        create_access_token,
    )

    _, record, store_id = await _mint(test_session)
    store = SimpleNamespace(
        id=store_id, tenant_id=record.tenant_id, owner_id=record.user_id
    )

    async def one_key(session, tenant_id):
        return ApiPolicy(True, 60, 10, 1000, 1)

    async def allowed(session, sid):
        return SimpleNamespace(allowed=True, plan="pro")

    monkeypatch.setattr(api_limits, "policy_for_tenant", one_key)
    monkeypatch.setattr(
        "src.api.v1.routes.stores.access_tokens.api_access_for_store", allowed
    )
    body = CreateAccessTokenRequest(name="Second", scopes=["orders:read"])
    with pytest.raises(HTTPException) as exc:
        await create_access_token(request=body, store=store, db=test_session)
    assert exc.value.detail["code"] == "api_key_limit_reached"


async def test_the_429_body_and_retry_after_survive_the_error_handler():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.api.middleware.error_handler import setup_exception_handlers

    app = FastAPI()
    setup_exception_handlers(app)
    decision = api_limits.LimitDecision(
        allowed=False,
        code=RATE_LIMITED,
        limit=60,
        remaining=0,
        reset=1760000000,
        retry_after=18,
    )

    @app.get("/x")
    async def throttled():
        raise HTTPException(
            status_code=429,
            detail={
                "code": RATE_LIMITED,
                "message": "Too many API requests.",
                "retry_after": 18,
            },
            headers=decision.headers(),
        )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        response = await client.get("/x")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "18"
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Reset"] == "1760000000"
    assert response.json()["error"] == {
        "code": "rate_limit_exceeded",
        "message": "Too many API requests.",
        "retry_after": 18,
    }

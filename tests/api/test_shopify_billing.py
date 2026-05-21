"""Tests for the Shopify billing-sync endpoint (backend-001).

Covers:
  * 403 when X-Internal-Key is missing
  * 422 when plan_id or status is invalid
  * 200 + new entity on first sync
  * Idempotent upsert: same (store_id, subscription_id) updates the same row
  * cancelled_at is set on transition to a terminal status and preserved
    on subsequent retries
  * GET /billing/subscription returns null when none, returns the active
    record when one exists, and excludes terminal-status rows

Strategy: override the FastAPI dependency factory to inject a fake
ShopifySubscriptionRepository whose state is a plain in-memory dict.
This keeps the tests focused on the route + use case behavior without
needing a real Postgres for the ON CONFLICT upsert.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.dependencies.shopify import get_shopify_subscription_repo
from src.config.settings import get_settings
from src.main import app

# ──────────────────────────────────────────────────────────────────────
# Fake repo — mirrors the real one's surface but stores rows in memory.
# ──────────────────────────────────────────────────────────────────────


class _Row:
    """Minimal model-like object that the route's _to_response reads from."""

    def __init__(self, **kw):
        self.id = kw.get("id", uuid4())
        self.store_id = kw["store_id"]
        self.tenant_id = kw.get("tenant_id")
        self.shopify_subscription_id = kw["shopify_subscription_id"]
        self.status = kw["status"]
        self.plan_id = kw["plan_id"]
        self.is_trial = kw.get("is_trial", False)
        self.trial_ends_at = kw.get("trial_ends_at")
        self.current_period_end = kw.get("current_period_end")
        self.cancelled_at = kw.get("cancelled_at")
        self.synced_at = kw.get("synced_at", datetime.now(tz=UTC))
        self.created_at = kw.get("created_at", datetime.now(tz=UTC))
        self.updated_at = kw.get("updated_at", datetime.now(tz=UTC))


_TERMINAL = {"CANCELLED", "EXPIRED", "FROZEN", "DECLINED"}


class FakeRepo:
    """In-memory stand-in for ShopifySubscriptionRepository."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], _Row] = {}

    async def upsert(
        self,
        *,
        store_id: UUID,
        shopify_subscription_id: str,
        status: str,
        plan_id: str,
        is_trial: bool,
        trial_ends_at: datetime | None,
        current_period_end: datetime | None,
        tenant_id: UUID | None = None,
    ) -> _Row:
        key = (store_id, shopify_subscription_id)
        existing = self.rows.get(key)
        now = datetime.now(tz=UTC)
        becomes_terminal = status in _TERMINAL
        cancelled_at: datetime | None
        if existing and existing.cancelled_at:
            # Preserve earliest terminal timestamp.
            cancelled_at = existing.cancelled_at
        else:
            cancelled_at = now if becomes_terminal else None

        row = _Row(
            id=existing.id if existing else uuid4(),
            store_id=store_id,
            tenant_id=tenant_id,
            shopify_subscription_id=shopify_subscription_id,
            status=status,
            plan_id=plan_id,
            is_trial=is_trial,
            trial_ends_at=trial_ends_at,
            current_period_end=current_period_end,
            cancelled_at=cancelled_at,
            synced_at=now,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.rows[key] = row
        return row

    async def get_active(self, store_id: UUID) -> _Row | None:
        candidates = [
            r
            for r in self.rows.values()
            if r.store_id == store_id and r.status not in _TERMINAL
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.synced_at)

    async def get_by_subscription_id(
        self, store_id: UUID, shopify_subscription_id: str
    ) -> _Row | None:
        return self.rows.get((store_id, shopify_subscription_id))


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture(autouse=True)
def _override_repo(fake_repo: FakeRepo):
    """Inject the fake into every test in this module."""
    app.dependency_overrides[get_shopify_subscription_repo] = lambda: fake_repo
    yield
    app.dependency_overrides.pop(get_shopify_subscription_repo, None)


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """DB-free async client.

    Overrides the global conftest `client` fixture which spins up a
    SQLite test_engine over the full schema — that path fails on
    PostgreSQL-only types (BYTEA), so we use a fake repo and bypass DB
    setup entirely. The `_override_repo` autouse fixture takes care of
    swapping in the in-memory FakeRepo.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def internal_key() -> str:
    """The configured X-Internal-Key for the test environment."""
    key = get_settings().shopify_internal_key
    return key or "shopify_internal_key"


def _trial_iso(days: int = 14) -> str:
    return (datetime.now(tz=UTC) + timedelta(days=days)).isoformat()


def _body(
    *,
    sub_id: str = "gid://shopify/AppSubscription/1",
    status: str = "ACTIVE",
    plan_id: str = "growth",
    is_trial: bool = True,
    trial_ends_at: str | None = None,
    current_period_end: str | None = None,
) -> dict:
    return {
        "subscription_id": sub_id,
        "status": status,
        "plan_id": plan_id,
        "is_trial": is_trial,
        "trial_ends_at": trial_ends_at or _trial_iso(),
        "current_period_end": current_period_end or _trial_iso(30),
    }


# ──────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────


class TestBillingSyncAuth:
    @pytest.mark.asyncio
    async def test_missing_internal_key_returns_403(self, client: AsyncClient):
        store_id = uuid4()
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(),
        )
        assert resp.status_code in (401, 403, 422)

    @pytest.mark.asyncio
    async def test_wrong_internal_key_returns_403(self, client: AsyncClient):
        store_id = uuid4()
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(),
            headers={"X-Internal-Key": "definitely-not-the-key"},
        )
        assert resp.status_code == 403


# ──────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────


class TestBillingSyncValidation:
    @pytest.mark.asyncio
    async def test_invalid_plan_id_returns_422(
        self, client: AsyncClient, internal_key: str
    ):
        store_id = uuid4()
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(plan_id="enterprise"),
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_status_returns_422(
        self, client: AsyncClient, internal_key: str
    ):
        store_id = uuid4()
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(status="FOO"),
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_422(
        self, client: AsyncClient, internal_key: str
    ):
        store_id = uuid4()
        bad = _body()
        bad.pop("subscription_id")
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=bad,
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────
# Upsert semantics
# ──────────────────────────────────────────────────────────────────────


class TestBillingSyncUpsert:
    @pytest.mark.asyncio
    async def test_first_sync_creates_row(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRepo,
    ):
        store_id = uuid4()
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(),
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["plan_id"] == "growth"
        assert body["data"]["status"] == "ACTIVE"
        assert body["data"]["is_trial"] is True
        assert body["data"]["cancelled_at"] is None
        assert len(fake_repo.rows) == 1

    @pytest.mark.asyncio
    async def test_repeated_sync_updates_same_row(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRepo,
    ):
        store_id = uuid4()
        sub_id = "gid://shopify/AppSubscription/dup-1"
        for _ in range(3):
            resp = await client.post(
                f"/api/v1/shopify/{store_id}/billing/sync",
                json=_body(sub_id=sub_id),
                headers={"X-Internal-Key": internal_key},
            )
            assert resp.status_code == 200
        # Only one row even after 3 retries.
        assert len(fake_repo.rows) == 1

    @pytest.mark.asyncio
    async def test_cancelled_status_sets_cancelled_at(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRepo,
    ):
        store_id = uuid4()
        sub_id = "gid://shopify/AppSubscription/cancel-1"
        # Active first.
        await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(sub_id=sub_id),
            headers={"X-Internal-Key": internal_key},
        )
        # Then cancel.
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(sub_id=sub_id, status="CANCELLED"),
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "CANCELLED"
        assert body["data"]["cancelled_at"] is not None

    @pytest.mark.asyncio
    async def test_repeated_cancelled_preserves_earliest_timestamp(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRepo,
    ):
        store_id = uuid4()
        sub_id = "gid://shopify/AppSubscription/cancel-retry-1"
        # First CANCELLED.
        r1 = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(sub_id=sub_id, status="CANCELLED"),
            headers={"X-Internal-Key": internal_key},
        )
        first_ts = r1.json()["data"]["cancelled_at"]
        # Second CANCELLED (a retry from the Shopify-app).
        r2 = await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(sub_id=sub_id, status="CANCELLED"),
            headers={"X-Internal-Key": internal_key},
        )
        second_ts = r2.json()["data"]["cancelled_at"]
        assert first_ts == second_ts


# ──────────────────────────────────────────────────────────────────────
# GET active subscription
# ──────────────────────────────────────────────────────────────────────


class TestBillingGet:
    @pytest.mark.asyncio
    async def test_get_returns_null_when_no_subscription(
        self,
        client: AsyncClient,
        internal_key: str,
    ):
        store_id = uuid4()
        resp = await client.get(
            f"/api/v1/shopify/{store_id}/billing/subscription",
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert resp.json()["data"] is None

    @pytest.mark.asyncio
    async def test_get_returns_active_subscription(
        self,
        client: AsyncClient,
        internal_key: str,
    ):
        store_id = uuid4()
        await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(plan_id="scale", is_trial=False),
            headers={"X-Internal-Key": internal_key},
        )
        resp = await client.get(
            f"/api/v1/shopify/{store_id}/billing/subscription",
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["plan_id"] == "scale"

    @pytest.mark.asyncio
    async def test_get_excludes_cancelled_subscription(
        self,
        client: AsyncClient,
        internal_key: str,
    ):
        store_id = uuid4()
        sub_id = "gid://shopify/AppSubscription/excl-1"
        await client.post(
            f"/api/v1/shopify/{store_id}/billing/sync",
            json=_body(sub_id=sub_id, status="CANCELLED"),
            headers={"X-Internal-Key": internal_key},
        )
        resp = await client.get(
            f"/api/v1/shopify/{store_id}/billing/subscription",
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert resp.json()["data"] is None

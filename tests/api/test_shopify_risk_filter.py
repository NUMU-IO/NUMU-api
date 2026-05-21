"""Tests for the shopify_order_id filter on /risk/orders (backend-002).

Strategy: same dependency-override pattern as test_shopify_billing.py.
A fake RiskAssessmentRepository whose list_by_store accepts the new
keyword argument and returns canned rows.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.dependencies.shopify import get_risk_assessment_repo
from src.config.settings import get_settings
from src.main import app

# ──────────────────────────────────────────────────────────────────────


class _Row:
    """Minimal stand-in for RiskAssessmentModel."""

    def __init__(self, **kw):
        self.id = kw.get("id", uuid4())
        self.store_id = kw["store_id"]
        self.shopify_order_id = kw["shopify_order_id"]
        self.order_number = kw.get("order_number", "1042")
        self.customer_name = kw.get("customer_name", "Test")
        self.customer_email = kw.get("customer_email", "t@example.com")
        self.total_cents = kw.get("total_cents", 1000)
        self.currency = kw.get("currency", "USD")
        self.payment_method = kw.get("payment_method", "cod")
        self.risk_score = kw.get("risk_score", 75)
        self.risk_level = kw.get("risk_level", "high")
        self.score_type = kw.get("score_type", "final")
        self.suggested_action = kw.get("suggested_action", "whatsapp_confirm")
        self.action_taken = kw.get("action_taken")
        self.factors = kw.get("factors", [])
        self.scored_at = kw.get("scored_at")
        self.created_at = kw.get("created_at", datetime.now(tz=UTC))


class FakeRiskRepo:
    """In-memory stand-in for RiskAssessmentRepository.list_by_store."""

    def __init__(self) -> None:
        self.rows: list[_Row] = []
        self.last_call: dict | None = None

    async def list_by_store(
        self,
        store_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        shopify_order_id: str | None = None,
    ) -> list[_Row]:
        self.last_call = {
            "store_id": store_id,
            "limit": limit,
            "offset": offset,
            "shopify_order_id": shopify_order_id,
        }
        out = [r for r in self.rows if r.store_id == store_id]
        if shopify_order_id is not None:
            out = [r for r in out if r.shopify_order_id == shopify_order_id]
        return out[offset : offset + limit]


@pytest.fixture
def fake_repo() -> FakeRiskRepo:
    return FakeRiskRepo()


@pytest.fixture(autouse=True)
def _override_repo(fake_repo: FakeRiskRepo):
    app.dependency_overrides[get_risk_assessment_repo] = lambda: fake_repo
    yield
    app.dependency_overrides.pop(get_risk_assessment_repo, None)


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def internal_key() -> str:
    return get_settings().shopify_internal_key or "shopify_internal_key"


# ──────────────────────────────────────────────────────────────────────


class TestShopifyOrderIdFilter:
    @pytest.mark.asyncio
    async def test_filter_returns_only_matching_row(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRiskRepo,
    ):
        store_id = uuid4()
        fake_repo.rows = [
            _Row(store_id=store_id, shopify_order_id="gid://shopify/Order/1"),
            _Row(store_id=store_id, shopify_order_id="gid://shopify/Order/2"),
            _Row(store_id=store_id, shopify_order_id="gid://shopify/Order/3"),
        ]
        resp = await client.get(
            f"/api/v1/shopify/{store_id}/risk/orders",
            params={"shopify_order_id": "gid://shopify/Order/2"},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        # The repo received the filter (verifying it isn't ignored).
        assert (
            fake_repo.last_call is not None
            and fake_repo.last_call["shopify_order_id"] == "gid://shopify/Order/2"
        )

    @pytest.mark.asyncio
    async def test_filter_returns_empty_list_when_no_match(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRiskRepo,
    ):
        store_id = uuid4()
        fake_repo.rows = [
            _Row(store_id=store_id, shopify_order_id="gid://shopify/Order/1"),
        ]
        resp = await client.get(
            f"/api/v1/shopify/{store_id}/risk/orders",
            params={"shopify_order_id": "gid://shopify/Order/nonexistent"},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        # Empty list, NOT 404 — Shopify-app's proxy interprets
        # empty as {status: "pending"}.
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_unfiltered_request_returns_full_list(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRiskRepo,
    ):
        store_id = uuid4()
        fake_repo.rows = [
            _Row(store_id=store_id, shopify_order_id=f"gid://shopify/Order/{i}")
            for i in range(5)
        ]
        resp = await client.get(
            f"/api/v1/shopify/{store_id}/risk/orders",
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 5
        # When omitted, the param reaches the repo as None — no
        # filtering applied.
        assert (
            fake_repo.last_call is not None
            and fake_repo.last_call["shopify_order_id"] is None
        )

    @pytest.mark.asyncio
    async def test_invalid_internal_key_is_still_403(
        self,
        client: AsyncClient,
        fake_repo: FakeRiskRepo,
    ):
        store_id = uuid4()
        resp = await client.get(
            f"/api/v1/shopify/{store_id}/risk/orders",
            params={"shopify_order_id": "gid://shopify/Order/1"},
            headers={"X-Internal-Key": "wrong-key"},
        )
        assert resp.status_code == 403

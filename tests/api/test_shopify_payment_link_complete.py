"""Tests for the hardened payment-link completion endpoint.

The endpoint is public (buyer-facing page has no credentials), so
completion must carry proof: X-Internal-Key OR a verified Paymob
callback. A bare session UUID must never mark a COD order paid.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.dependencies.database import get_db
from src.api.dependencies.shopify import (
    get_payment_link_session_repo,
    get_shopify_installation_repo,
)
from src.config.settings import get_settings
from src.main import app


class _Session:
    def __init__(self, **kw):
        self.id = kw.get("id", uuid4())
        self.store_id = kw.get("store_id", uuid4())
        self.shopify_order_id = kw.get("shopify_order_id", "990011")
        self.amount_cents = kw.get("amount_cents", 15000)
        self.currency = kw.get("currency", "EGP")
        self.status = kw.get("status", "pending")
        self.expires_at = kw.get("expires_at", datetime.now(UTC) + timedelta(hours=1))


class FakePaymentLinkRepo:
    def __init__(self) -> None:
        self.sessions: dict[UUID, _Session] = {}
        self.completed: list[dict] = []

    async def get_by_id(self, session_id: UUID) -> _Session | None:
        return self.sessions.get(session_id)

    async def mark_completed(self, session_id: UUID, **kw) -> _Session | None:
        model = self.sessions.get(session_id)
        if model:
            model.status = "completed"
            self.completed.append({"session_id": session_id, **kw})
        return model


class FakeInstallRepo:
    async def get_by_store_id(self, store_id: UUID):
        return None  # skip the Shopify tag/note mutations


class FakeDb:
    async def execute(self, _q):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self):
        return None


@pytest.fixture
def repo() -> FakePaymentLinkRepo:
    return FakePaymentLinkRepo()


@pytest.fixture(autouse=True)
def _overrides(repo: FakePaymentLinkRepo):
    app.dependency_overrides[get_payment_link_session_repo] = lambda: repo
    app.dependency_overrides[get_shopify_installation_repo] = lambda: FakeInstallRepo()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    yield
    app.dependency_overrides.pop(get_payment_link_session_repo, None)
    app.dependency_overrides.pop(get_shopify_installation_repo, None)
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def internal_key() -> str:
    return get_settings().shopify_internal_key or "shopify_internal_key"


BODY = {"gateway_used": "paymob", "gateway_transaction_id": "txn-1"}


class TestCompletionAuth:
    @pytest.mark.asyncio
    async def test_bare_uuid_is_401(
        self, client: AsyncClient, repo: FakePaymentLinkRepo
    ):
        s = _Session()
        repo.sessions[s.id] = s
        resp = await client.post(
            f"/api/v1/shopify/payment-links/{s.id}/complete", json=BODY
        )
        assert resp.status_code == 401
        assert s.status == "pending"
        assert repo.completed == []

    @pytest.mark.asyncio
    async def test_wrong_internal_key_is_401(
        self, client: AsyncClient, repo: FakePaymentLinkRepo
    ):
        s = _Session()
        repo.sessions[s.id] = s
        resp = await client.post(
            f"/api/v1/shopify/payment-links/{s.id}/complete",
            json=BODY,
            headers={"X-Internal-Key": "wrong"},
        )
        assert resp.status_code == 401
        assert s.status == "pending"

    @pytest.mark.asyncio
    async def test_unknown_session_unauthenticated_is_401_not_404(
        self, client: AsyncClient
    ):
        """No existence oracle for unauthenticated probes."""
        resp = await client.post(
            f"/api/v1/shopify/payment-links/{uuid4()}/complete", json=BODY
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_internal_key_completes(
        self, client: AsyncClient, repo: FakePaymentLinkRepo, internal_key: str
    ):
        s = _Session()
        repo.sessions[s.id] = s
        resp = await client.post(
            f"/api/v1/shopify/payment-links/{s.id}/complete",
            json=BODY,
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert s.status == "completed"
        assert repo.completed[0]["gateway_transaction_id"] == "txn-1"

    @pytest.mark.asyncio
    async def test_internal_key_unknown_session_is_404(
        self, client: AsyncClient, internal_key: str
    ):
        resp = await client.post(
            f"/api/v1/shopify/payment-links/{uuid4()}/complete",
            json=BODY,
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 404


class TestPaymobProofPath:
    def _paymob_body(self, amount_cents: int = 15000) -> dict:
        return {
            **BODY,
            "paymob_payload": {
                "type": "TRANSACTION",
                "obj": {"id": 987654, "amount_cents": amount_cents, "success": True},
            },
            "paymob_hmac": "aa" * 32,
        }

    @pytest.mark.asyncio
    async def test_verified_paymob_proof_completes(
        self, client: AsyncClient, repo: FakePaymentLinkRepo, monkeypatch
    ):
        s = _Session(amount_cents=15000)
        repo.sessions[s.id] = s

        import src.application.services.shopify_paymob_verification as svc

        async def _accept(db, **kw):
            assert kw["store_id"] == s.store_id
            assert kw["expected_amount_cents"] == 15000
            return True, "ok"

        monkeypatch.setattr(svc, "verify_paymob_completion", _accept)

        resp = await client.post(
            f"/api/v1/shopify/payment-links/{s.id}/complete",
            json=self._paymob_body(),
        )
        assert resp.status_code == 200
        assert s.status == "completed"
        # Transaction identity comes from the VERIFIED payload, not the
        # caller-supplied gateway_transaction_id.
        assert repo.completed[0]["gateway_transaction_id"] == "987654"

    @pytest.mark.asyncio
    async def test_rejected_signature_is_401(
        self, client: AsyncClient, repo: FakePaymentLinkRepo, monkeypatch
    ):
        s = _Session()
        repo.sessions[s.id] = s

        import src.application.services.shopify_paymob_verification as svc

        async def _reject(db, **kw):
            return False, svc.REASON_INVALID_SIGNATURE

        monkeypatch.setattr(svc, "verify_paymob_completion", _reject)

        resp = await client.post(
            f"/api/v1/shopify/payment-links/{s.id}/complete",
            json=self._paymob_body(),
        )
        assert resp.status_code == 401
        assert s.status == "pending"

    @pytest.mark.asyncio
    async def test_amount_mismatch_is_400(
        self, client: AsyncClient, repo: FakePaymentLinkRepo, monkeypatch
    ):
        s = _Session(amount_cents=15000)
        repo.sessions[s.id] = s

        import src.application.services.shopify_paymob_verification as svc

        async def _mismatch(db, **kw):
            return False, svc.REASON_AMOUNT_MISMATCH

        monkeypatch.setattr(svc, "verify_paymob_completion", _mismatch)

        resp = await client.post(
            f"/api/v1/shopify/payment-links/{s.id}/complete",
            json=self._paymob_body(amount_cents=100),
        )
        assert resp.status_code == 400
        assert s.status == "pending"


class TestWebhookContributionIdempotency:
    """B4 structural guard: every shopify-webhook network write carries a
    dedup_key, else Shopify redeliveries double-count into the local
    graph AND the standalone Trust Network feed."""

    def test_all_network_writes_pass_dedup_key(self):
        import inspect

        import src.api.v1.routes.shopify.webhooks as wh

        source = inspect.getsource(wh)
        calls = source.split("_write_network_event(")[1:]
        # First hit is the import alias line; real call sites parse args.
        call_sites = [c for c in calls if "phone_hash=" in c[:400]]
        assert len(call_sites) >= 2, "expected both network-write call sites"
        for site in call_sites:
            assert "dedup_key=" in site[:600], (
                "a _write_network_event call site is missing dedup_key"
            )

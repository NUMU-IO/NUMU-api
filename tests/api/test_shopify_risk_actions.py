"""Tests for the risk action verb whitelist, the manual whatsapp_confirm
nudge dispatch, and the resend-verification endpoint (A1/A2/A7 wiring).

Same dependency-override pattern as test_shopify_risk_filter.py — fake
repositories, monkeypatched Shopify/WhatsApp seams, no DB.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.dependencies.database import get_db
from src.api.dependencies.shopify import (
    get_risk_assessment_repo,
    get_shopify_installation_repo,
)
from src.config.settings import get_settings
from src.main import app

# ──────────────────────────────────────────────────────────────────────


class _Row:
    """Minimal stand-in for RiskAssessmentModel."""

    def __init__(self, **kw):
        self.id = kw.get("id", uuid4())
        self.store_id = kw["store_id"]
        self.shopify_order_id = kw.get("shopify_order_id", "990011")
        self.order_number = kw.get("order_number", "1042")
        self.customer_name = kw.get("customer_name", "Test Buyer")
        self.customer_email = kw.get("customer_email", "t@example.com")
        self.total_cents = kw.get("total_cents", 15000)
        self.currency = kw.get("currency", "EGP")
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
    def __init__(self) -> None:
        self.rows: list[_Row] = []

    async def list_by_store(
        self,
        store_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        shopify_order_id: str | None = None,
    ) -> list[_Row]:
        out = [r for r in self.rows if r.store_id == store_id]
        if shopify_order_id is not None:
            out = [r for r in out if r.shopify_order_id == shopify_order_id]
        return out[offset : offset + limit]

    async def update_action(
        self, assessment_id: UUID, action: str, *, store_id: UUID | None = None
    ) -> _Row | None:
        for r in self.rows:
            if r.id == assessment_id:
                if store_id is not None and str(r.store_id) != str(store_id):
                    return None
                r.action_taken = action
                return r
        return None


class FakeInstallRepo:
    def __init__(self, installation=None) -> None:
        self.installation = installation

    async def get_by_store_id(self, store_id: UUID):
        return self.installation


class FakeDbSession:
    async def commit(self) -> None:
        return None


@pytest.fixture
def fake_repo() -> FakeRiskRepo:
    return FakeRiskRepo()


@pytest.fixture
def fake_install_repo() -> FakeInstallRepo:
    return FakeInstallRepo(
        SimpleNamespace(
            shopify_domain="demo.myshopify.com",
            access_token_encrypted="shpat_test",
        )
    )


@pytest.fixture(autouse=True)
def _override_deps(fake_repo: FakeRiskRepo, fake_install_repo: FakeInstallRepo):
    app.dependency_overrides[get_risk_assessment_repo] = lambda: fake_repo
    app.dependency_overrides[get_shopify_installation_repo] = lambda: fake_install_repo
    app.dependency_overrides[get_db] = lambda: FakeDbSession()
    yield
    app.dependency_overrides.pop(get_risk_assessment_repo, None)
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


@pytest.fixture
def contact(monkeypatch) -> dict:
    """Patch the Shopify order-contact fetch at its source module."""
    data = {
        "phone": "+201001234567",
        "customer_name": "Shopify Name",
        "order_number": "1042",
        "shop_name": "Demo Store",
    }

    async def _fake_contact(shop_domain, access_token, shopify_order_id):
        return data

    import src.infrastructure.external_services.shopify.admin_client as ac

    monkeypatch.setattr(ac, "get_order_contact", _fake_contact)
    return data


# ──────────────────────────────────────────────────────────────────────


class TestActionVerbWhitelist:
    @pytest.mark.asyncio
    async def test_unknown_verb_is_422(
        self, client: AsyncClient, internal_key: str, fake_repo: FakeRiskRepo
    ):
        store_id = uuid4()
        row = _Row(store_id=store_id)
        fake_repo.rows = [row]
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/risk/orders/{row.id}/action",
            json={"action": "auto_approve"},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cross_store_action_is_404(
        self, client: AsyncClient, internal_key: str, fake_repo: FakeRiskRepo
    ):
        """A valid key + a known assessment UUID must not act across stores."""
        owner_store = uuid4()
        other_store = uuid4()
        row = _Row(store_id=owner_store)
        fake_repo.rows = [row]
        resp = await client.post(
            f"/api/v1/shopify/{other_store}/risk/orders/{row.id}/action",
            json={"action": "approve"},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 404
        assert row.action_taken is None

    @pytest.mark.asyncio
    async def test_approve_records_action(
        self, client: AsyncClient, internal_key: str, fake_repo: FakeRiskRepo
    ):
        store_id = uuid4()
        row = _Row(store_id=store_id)
        fake_repo.rows = [row]
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/risk/orders/{row.id}/action",
            json={"action": "approve"},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert row.action_taken == "approve"
        assert resp.json()["data"]["action_taken"] == "approve"


class TestManualWhatsappConfirm:
    @pytest.mark.asyncio
    async def test_whatsapp_confirm_queues_nudge(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRiskRepo,
        contact: dict,
        monkeypatch,
    ):
        store_id = uuid4()
        row = _Row(store_id=store_id)
        fake_repo.rows = [row]

        queued: list[dict] = []
        import src.infrastructure.messaging.tasks.whatsapp_nudge_task as nt

        monkeypatch.setattr(
            nt.send_whatsapp_nudge, "delay", lambda **kw: queued.append(kw)
        )

        resp = await client.post(
            f"/api/v1/shopify/{store_id}/risk/orders/{row.id}/action",
            json={"action": "whatsapp_confirm"},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert row.action_taken == "whatsapp_confirm"
        assert len(queued) == 1
        assert queued[0]["customer_phone"] == contact["phone"]
        assert queued[0]["shopify_order_id"] == row.shopify_order_id
        assert "queued" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_whatsapp_confirm_without_phone_still_records(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRiskRepo,
        monkeypatch,
    ):
        store_id = uuid4()
        row = _Row(store_id=store_id)
        fake_repo.rows = [row]

        async def _no_contact(*a, **kw):
            return None

        import src.infrastructure.external_services.shopify.admin_client as ac

        monkeypatch.setattr(ac, "get_order_contact", _no_contact)

        resp = await client.post(
            f"/api/v1/shopify/{store_id}/risk/orders/{row.id}/action",
            json={"action": "whatsapp_confirm"},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert row.action_taken == "whatsapp_confirm"
        assert "skipped" in resp.json()["message"]


class TestResendVerification:
    @pytest.mark.asyncio
    async def test_resend_404_when_no_assessment(
        self, client: AsyncClient, internal_key: str
    ):
        store_id = uuid4()
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/risk/orders/424242/resend-verification",
            json={},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_resend_happy_path_returns_message_id(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRiskRepo,
        contact: dict,
        monkeypatch,
    ):
        store_id = uuid4()
        row = _Row(store_id=store_id, shopify_order_id="424242")
        fake_repo.rows = [row]

        import src.application.services.shopify_nudge_service as svc

        pls_id = uuid4()

        async def _fake_create(session, **kw):
            return SimpleNamespace(id=pls_id)

        sent: list[dict] = []

        async def _fake_send(**kw):
            sent.append(kw)
            return svc.NudgeSendResult(
                sent=True,
                session_id=kw["payment_session_id"],
                payment_url=f"https://shopify.numueg.app/p/{pls_id}",
                message_id="wamid.TEST",
            )

        monkeypatch.setattr(svc, "create_payment_link_session", _fake_create)
        monkeypatch.setattr(svc, "send_conversion_nudge", _fake_send)

        resp = await client.post(
            f"/api/v1/shopify/{store_id}/risk/orders/424242/resend-verification",
            json={"language": "en"},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sent"] is True
        assert data["message_id"] == "wamid.TEST"
        assert sent[0]["language"] == "en"
        assert sent[0]["payment_session_id"] == str(pls_id)

    @pytest.mark.asyncio
    async def test_resend_accepts_gid_form(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRiskRepo,
        contact: dict,
        monkeypatch,
    ):
        """Rows store the numeric id; a gid in the URL must still match."""
        store_id = uuid4()
        row = _Row(store_id=store_id, shopify_order_id="424242")
        fake_repo.rows = [row]

        import src.application.services.shopify_nudge_service as svc

        async def _fake_create(session, **kw):
            return SimpleNamespace(id=uuid4())

        async def _fake_send(**kw):
            return svc.NudgeSendResult(
                sent=True, session_id="x", payment_url="u", message_id="wamid.G"
            )

        monkeypatch.setattr(svc, "create_payment_link_session", _fake_create)
        monkeypatch.setattr(svc, "send_conversion_nudge", _fake_send)

        gid = "gid:%2F%2Fshopify%2FOrder%2F424242"
        resp = await client.post(
            f"/api/v1/shopify/{store_id}/risk/orders/{gid}/resend-verification",
            json={},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["sent"] is True

    @pytest.mark.asyncio
    async def test_resend_no_phone_reports_reason(
        self,
        client: AsyncClient,
        internal_key: str,
        fake_repo: FakeRiskRepo,
        monkeypatch,
    ):
        store_id = uuid4()
        row = _Row(store_id=store_id, shopify_order_id="424242")
        fake_repo.rows = [row]

        async def _no_phone(*a, **kw):
            return {"phone": None}

        import src.infrastructure.external_services.shopify.admin_client as ac

        monkeypatch.setattr(ac, "get_order_contact", _no_phone)

        resp = await client.post(
            f"/api/v1/shopify/{store_id}/risk/orders/424242/resend-verification",
            json={},
            headers={"X-Internal-Key": internal_key},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sent"] is False
        assert data["reason"] == "no_phone"

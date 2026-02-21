"""Integration tests for the Fawry webhook endpoint.

Tests the full POST /webhooks/fawry/callback flow through FastAPI,
mocking external dependencies (Redis, WhatsApp) but exercising the
full request → service → DB pipeline.
"""

import hashlib
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.fawry_webhook_service import MAX_WEBHOOK_AGE_SECONDS
from src.core.entities.order import OrderStatus, PaymentStatus
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.database.models.tenant.order import OrderModel
from src.main import app


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAWRY_SECURITY_KEY = "test-secret-key"
WEBHOOK_URL = "/api/v1/webhooks/fawry/callback"


def _build_fawry_payload(
    merchant_ref: str = "MERCHANT-REF-001",
    reference_number: str = "FAWRY-REF-001",
    order_status: str = "PAID",
    payment_amount: float = 250.00,
    payment_method: str = "PAYATFAWRY",
    fawry_fees: float = 5.00,
    **extra,
) -> dict:
    """Build a Fawry webhook payload."""
    payload = {
        "referenceNumber": reference_number,
        "merchantRefNum": merchant_ref,
        "orderStatus": order_status,
        "paymentAmount": payment_amount,
        "orderAmount": payment_amount,
        "paymentMethod": payment_method,
        "fawryFees": fawry_fees,
        "shippingFees": "",
        "authNumber": "",
        "customerMail": "customer@example.com",
        "customerMobile": "+201234567890",
    }
    payload.update(extra)
    return payload


def _sign_payload(payload: dict, security_key: str = FAWRY_SECURITY_KEY) -> str:
    """Compute the Fawry SHA-256 webhook signature."""
    sig_parts = [
        str(payload.get("referenceNumber", "")),
        str(payload.get("merchantRefNum", "")),
        str(payload.get("paymentAmount", "")),
        str(payload.get("orderAmount", "")),
        str(payload.get("orderStatus", "")),
        str(payload.get("paymentMethod", "")),
        str(payload.get("fawryFees", "") or ""),
        str(payload.get("shippingFees", "") or ""),
        str(payload.get("authNumber", "") or ""),
        str(payload.get("customerMail", "") or ""),
        str(payload.get("customerMobile", "") or ""),
        security_key,
    ]
    return hashlib.sha256("".join(sig_parts).encode()).hexdigest()


def _make_order_model(
    merchant_ref: str = "MERCHANT-REF-001",
    order_status: OrderStatus = OrderStatus.PENDING,
    payment_status: PaymentStatus = PaymentStatus.PENDING,
) -> MagicMock:
    """Create a mock OrderModel for DB seeding."""
    model = MagicMock(spec=OrderModel)
    model.id = uuid4()
    model.order_number = "ORD-000001"
    model.store_id = uuid4()
    model.tenant_id = uuid4()
    model.customer_id = uuid4()
    model.status = order_status
    model.payment_status = payment_status
    model.payment_method = "fawry"
    model.payment_id = merchant_ref
    model.paid_at = None
    model.cancelled_at = None
    model.extra_data = {}
    model.total = 25000
    model.currency = "EGP"
    model.line_items = [
        {"product_id": str(uuid4()), "quantity": 2, "product_name": "Widget"}
    ]
    store = MagicMock()
    store.name = "Test Store"
    store.contact_phone = "+201234567890"
    store.default_language = "en"
    model.store = store
    return model


def _mock_db_session(order=None) -> AsyncMock:
    """Create a mock AsyncSession wired to return ``order`` on lookup."""
    session = AsyncMock(spec=AsyncSession)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = order
    session.execute.return_value = result_mock
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


async def _post_webhook(
    payload: dict,
    db_session: AsyncMock | None = None,
    headers: dict | None = None,
):
    """Send a POST to the webhook endpoint with standard mocking.

    Shared helper to reduce boilerplate across tests.
    """
    session = db_session or _mock_db_session()

    async def override_get_admin_db():
        yield session

    app.dependency_overrides[get_admin_db_session] = override_get_admin_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("src.api.v1.routes.webhooks.fawry.settings") as mock_settings, \
             patch("src.api.v1.routes.webhooks.fawry._cache_service", None), \
             patch("src.api.v1.routes.webhooks.fawry._messaging_service", None):
            mock_settings.fawry_security_key = None
            mock_settings.redis_host = None
            mock_settings.whatsapp_enabled = False
            response = await ac.post(
                WEBHOOK_URL,
                content=json.dumps(payload),
                headers=headers or {"Content-Type": "application/json"},
            )

    app.dependency_overrides.clear()
    return response


# ---------------------------------------------------------------------------
# Happy-path tests: all four statuses return 200
# ---------------------------------------------------------------------------


class TestFawryWebhookHappyPath:
    """Each status webhook returns 200 with status=received."""

    @pytest.mark.asyncio
    async def test_paid_webhook_returns_200(self):
        order = _make_order_model()
        response = await _post_webhook(
            _build_fawry_payload(order_status="PAID"),
            db_session=_mock_db_session(order),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "received"
        assert body["order_status"] == "PAID"

    @pytest.mark.asyncio
    async def test_expired_webhook_returns_200(self):
        order = _make_order_model()
        response = await _post_webhook(
            _build_fawry_payload(order_status="EXPIRED"),
            db_session=_mock_db_session(order),
        )
        assert response.status_code == 200
        assert response.json()["order_status"] == "EXPIRED"

    @pytest.mark.asyncio
    async def test_canceled_webhook_returns_200(self):
        order = _make_order_model()
        response = await _post_webhook(
            _build_fawry_payload(order_status="CANCELED"),
            db_session=_mock_db_session(order),
        )
        assert response.status_code == 200
        assert response.json()["order_status"] == "CANCELED"

    @pytest.mark.asyncio
    async def test_refunded_webhook_returns_200(self):
        order = _make_order_model(payment_status=PaymentStatus.PAID)
        response = await _post_webhook(
            _build_fawry_payload(order_status="REFUNDED"),
            db_session=_mock_db_session(order),
        )
        assert response.status_code == 200
        assert response.json()["order_status"] == "REFUNDED"

    @pytest.mark.asyncio
    async def test_new_status_returns_200_no_processing(self):
        response = await _post_webhook(
            _build_fawry_payload(order_status="NEW"),
        )
        assert response.status_code == 200
        assert response.json()["order_status"] == "NEW"


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------


class TestReplayProtection:
    """Duplicate webhook returns status=duplicate."""

    @pytest.mark.asyncio
    async def test_duplicate_webhook_returns_duplicate_status(self):
        session = _mock_db_session()

        async def override_get_admin_db():
            yield session

        app.dependency_overrides[get_admin_db_session] = override_get_admin_db

        payload = _build_fawry_payload(order_status="PAID")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("src.api.v1.routes.webhooks.fawry.settings") as mock_settings, \
                 patch("src.api.v1.routes.webhooks.fawry._cache_service") as mock_cache, \
                 patch("src.api.v1.routes.webhooks.fawry._messaging_service", None):
                mock_settings.fawry_security_key = None
                mock_settings.redis_host = "localhost"
                mock_settings.whatsapp_enabled = False

                # Simulate duplicate: set_if_absent returns False
                mock_cache.set_if_absent = AsyncMock(return_value=False)

                response = await ac.post(
                    WEBHOOK_URL,
                    content=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["status"] == "duplicate"


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    """Invalid signatures should return 401."""

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self):
        session = _mock_db_session()

        async def override_get_admin_db():
            yield session

        app.dependency_overrides[get_admin_db_session] = override_get_admin_db

        payload = _build_fawry_payload(order_status="PAID")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("src.api.v1.routes.webhooks.fawry.settings") as mock_settings, \
                 patch("src.api.v1.routes.webhooks.fawry._cache_service", None), \
                 patch("src.api.v1.routes.webhooks.fawry._messaging_service", None), \
                 patch("src.api.v1.routes.webhooks.fawry.fawry_service") as mock_fawry:
                mock_settings.fawry_security_key = FAWRY_SECURITY_KEY
                mock_settings.redis_host = None
                mock_settings.whatsapp_enabled = False

                # Signature verification returns None (invalid)
                mock_fawry.verify_webhook_signature.return_value = None

                response = await ac.post(
                    WEBHOOK_URL,
                    content=json.dumps(payload),
                    headers={
                        "Content-Type": "application/json",
                        "x-fawry-signature": "bad-signature",
                    },
                )

        app.dependency_overrides.clear()

        assert response.status_code == 401
        assert "Invalid webhook signature" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self):
        order = _make_order_model()
        session = _mock_db_session(order)

        async def override_get_admin_db():
            yield session

        app.dependency_overrides[get_admin_db_session] = override_get_admin_db

        payload = _build_fawry_payload(order_status="PAID")
        signature = _sign_payload(payload)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("src.api.v1.routes.webhooks.fawry.settings") as mock_settings, \
                 patch("src.api.v1.routes.webhooks.fawry._cache_service", None), \
                 patch("src.api.v1.routes.webhooks.fawry._messaging_service", None), \
                 patch("src.api.v1.routes.webhooks.fawry.fawry_service") as mock_fawry:
                mock_settings.fawry_security_key = FAWRY_SECURITY_KEY
                mock_settings.redis_host = None
                mock_settings.whatsapp_enabled = False

                # Signature verification returns parsed payload
                mock_fawry.verify_webhook_signature.return_value = payload

                response = await ac.post(
                    WEBHOOK_URL,
                    content=json.dumps(payload),
                    headers={
                        "Content-Type": "application/json",
                        "x-fawry-signature": signature,
                    },
                )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["status"] == "received"


# ---------------------------------------------------------------------------
# Timestamp freshness
# ---------------------------------------------------------------------------


class TestTimestampFreshness:
    """Stale webhooks should return 400."""

    @pytest.mark.asyncio
    async def test_stale_timestamp_returns_400(self):
        session = _mock_db_session()

        async def override_get_admin_db():
            yield session

        app.dependency_overrides[get_admin_db_session] = override_get_admin_db

        stale_ts = int((time.time() - MAX_WEBHOOK_AGE_SECONDS - 120) * 1000)
        payload = _build_fawry_payload(order_status="PAID", timestamp=stale_ts)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("src.api.v1.routes.webhooks.fawry.settings") as mock_settings, \
                 patch("src.api.v1.routes.webhooks.fawry._cache_service", None), \
                 patch("src.api.v1.routes.webhooks.fawry._messaging_service", None):
                mock_settings.fawry_security_key = None
                mock_settings.redis_host = None
                mock_settings.whatsapp_enabled = False

                response = await ac.post(
                    WEBHOOK_URL,
                    content=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )

        app.dependency_overrides.clear()

        assert response.status_code == 400
        assert "timestamp" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Malformed payload
# ---------------------------------------------------------------------------


class TestMalformedPayload:
    """Invalid JSON should return an error."""

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self):
        session = _mock_db_session()

        async def override_get_admin_db():
            yield session

        app.dependency_overrides[get_admin_db_session] = override_get_admin_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("src.api.v1.routes.webhooks.fawry.settings") as mock_settings, \
                 patch("src.api.v1.routes.webhooks.fawry._cache_service", None), \
                 patch("src.api.v1.routes.webhooks.fawry._messaging_service", None):
                mock_settings.fawry_security_key = None
                mock_settings.redis_host = None
                mock_settings.whatsapp_enabled = False

                response = await ac.post(
                    WEBHOOK_URL,
                    content="this is not json{{{",
                    headers={"Content-Type": "application/json"},
                )

        app.dependency_overrides.clear()

        # json.loads on invalid payload raises → 500
        assert response.status_code == 500

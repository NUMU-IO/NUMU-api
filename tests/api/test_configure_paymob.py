"""Tests for backend-018 Paymob settings real integration.

Pins the configure_paymob_credentials behavior — the validation +
persistence boundary that turns the previous stub into a real
integration. Uses httpx.MockTransport for the Paymob HTTP boundary
so tests don't hit the live API but DO exercise the real client
code path.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest

from src.application.use_cases.shopify.configure_paymob import (
    ConfigureFailure,
    configure_paymob_credentials,
)


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ─────────────────────────────────────────────────────────────────────
# Validation outcomes
# ─────────────────────────────────────────────────────────────────────


class TestPaymobRejected:
    @pytest.mark.asyncio
    async def test_401_returns_failure_with_status(self):
        """Paymob 401 → wrong secret_key. The route turns this into a
        422 to the merchant — that's the merchant's lever to fix."""

        async def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "Invalid token"})

        result = await configure_paymob_credentials(
            session=_DummySession(),
            store_id=uuid4(),
            secret_key="wrong-key",
            public_key="pub",
            hmac_secret="h",
            card_integration_id="123",
            http_client=_client_with(handler),
        )
        assert isinstance(result, ConfigureFailure)
        assert result.status_code == 401
        assert "paymob_rejected" in result.reason

    @pytest.mark.asyncio
    async def test_500_treated_as_rejection(self):
        async def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Paymob is down")

        result = await configure_paymob_credentials(
            session=_DummySession(),
            store_id=uuid4(),
            secret_key="x",
            public_key="x",
            hmac_secret="x",
            card_integration_id="1",
            http_client=_client_with(handler),
        )
        assert isinstance(result, ConfigureFailure)
        assert result.status_code == 500

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self):
        async def handler(_req: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        result = await configure_paymob_credentials(
            session=_DummySession(),
            store_id=uuid4(),
            secret_key="x",
            public_key="x",
            hmac_secret="x",
            card_integration_id="1",
            http_client=_client_with(handler),
        )
        assert isinstance(result, ConfigureFailure)
        assert "paymob_timeout" in result.reason

    @pytest.mark.asyncio
    async def test_network_error_returns_failure(self):
        async def handler(_req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        result = await configure_paymob_credentials(
            session=_DummySession(),
            store_id=uuid4(),
            secret_key="x",
            public_key="x",
            hmac_secret="x",
            card_integration_id="1",
            http_client=_client_with(handler),
        )
        assert isinstance(result, ConfigureFailure)
        assert "paymob_unreachable" in result.reason

    @pytest.mark.asyncio
    async def test_200_without_intention_data_treated_as_failure(self):
        """If Paymob returns 200 but no client_secret/intention_detail,
        something is off (mocked/staging response, schema drift). Don't
        flip ``paymob_connected`` true on a malformed success."""

        async def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        result = await configure_paymob_credentials(
            session=_DummySession(),
            store_id=uuid4(),
            secret_key="x",
            public_key="x",
            hmac_secret="x",
            card_integration_id="1",
            http_client=_client_with(handler),
        )
        assert isinstance(result, ConfigureFailure)
        assert "paymob_response_missing_intention" in result.reason


# ─────────────────────────────────────────────────────────────────────
# Validation request shape
# ─────────────────────────────────────────────────────────────────────


class TestRequestShape:
    @pytest.mark.asyncio
    async def test_request_includes_test_flag_and_minimal_amount(self):
        """The validation charge MUST be marked is_test:true and use
        the smallest legal Paymob amount (1 piaster). Otherwise we'd
        be running real-money charges every time a merchant connects."""
        captured: dict[str, Any] = {}

        async def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            captured["body"] = _json.loads(req.content.decode())
            captured["auth"] = req.headers.get("authorization")
            # Return failure so we don't reach persistence
            return httpx.Response(401, json={"detail": "fake"})

        await configure_paymob_credentials(
            session=_DummySession(),
            store_id=uuid4(),
            secret_key="my-secret",
            public_key="pub",
            hmac_secret="h",
            card_integration_id="42",
            http_client=_client_with(handler),
        )
        assert captured["body"]["amount"] == 1
        assert captured["body"]["is_test"] is True
        assert captured["body"]["payment_methods"] == [42]
        assert captured["auth"] == "Token my-secret"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


class _DummySession:
    """Minimal session stub. None of the failure tests reach the
    persistence step (Paymob fails first), so the real session graph
    is never exercised. Success path testing belongs in an integration
    test against a real Postgres."""

    async def execute(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError(
            "session.execute should not be called when validation fails"
        )

    async def flush(self):  # pragma: no cover
        raise AssertionError("session.flush should not be called when validation fails")

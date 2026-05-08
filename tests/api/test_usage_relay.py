"""Tests for UsageRelayService (backend-004).

Strategy: inject httpx.MockTransport via a factory so tests don't make
real HTTP calls. Verifies:
  * Correct URL + body shape + X-Internal-Key header.
  * Status mapping: 200 → RelayResult, 401 → RelayConfigError,
    422 → RelayInvalidPayload, network/timeout/5xx → RelayUnavailable.
  * Capped + recorded outcomes propagate through to the typed result.
  * Missing settings raise RelayConfigError.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.application.services.usage_relay_service import (
    RelayConfigError,
    RelayInvalidPayload,
    RelayResult,
    RelayUnavailable,
    UsageRelayService,
)
from src.config.settings import get_settings

# ──────────────────────────────────────────────────────────────────────


def _patch_settings(monkeypatch, *, base_url: str, key: str) -> None:
    """Override the cached Settings instance for one test."""
    settings = get_settings()
    monkeypatch.setattr(settings, "shopify_app_url", base_url, raising=False)
    monkeypatch.setattr(settings, "shopify_internal_key", key, raising=False)


def _factory_for(handler: httpx.MockTransport) -> Any:
    """Wrap a MockTransport in a `client_factory(timeout=...)` callable."""

    class _ScopedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs) -> None:
            kwargs["transport"] = handler
            super().__init__(*args, **kwargs)

    return _ScopedClient


# ──────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────


class TestPostUsageHappyPath:
    @pytest.mark.asyncio
    async def test_posts_to_correct_url_with_correct_body_and_header(self, monkeypatch):
        _patch_settings(
            monkeypatch,
            base_url="https://shopify.test",
            key="test-internal-key",
        )

        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = request.content
            return httpx.Response(
                200,
                json={
                    "recorded": True,
                    "capped": False,
                    "shopifyUsageRecordId": "gid://shopify/AppUsageRecord/1",
                },
            )

        transport = httpx.MockTransport(handler)
        service = UsageRelayService(client_factory=_factory_for(transport))

        result = await service.post_usage(
            shop_domain="example.myshopify.com",
            amount_cents=234,
            description="47 WhatsApp verifications above Growth cap",
            idempotency_key="key-abc-123",
        )

        assert result == RelayResult(
            recorded=True,
            capped=False,
            shopify_usage_record_id="gid://shopify/AppUsageRecord/1",
        )
        assert captured["url"] == "https://shopify.test/api/billing/usage-record"
        assert captured["headers"]["x-internal-key"] == "test-internal-key"

        # httpx serializes with no space after the colon; assert via
        # parsed JSON to be agnostic to formatting.
        import json

        body = json.loads(captured["body"].decode())
        assert body == {
            "shop_domain": "example.myshopify.com",
            "amount_cents": 234,
            "description": "47 WhatsApp verifications above Growth cap",
            "idempotency_key": "key-abc-123",
        }

    @pytest.mark.asyncio
    async def test_capped_response_propagates_to_result(self, monkeypatch):
        _patch_settings(
            monkeypatch,
            base_url="https://shopify.test",
            key="k",
        )

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"recorded": False, "capped": True, "reason": "cap_reached"},
            )

        service = UsageRelayService(
            client_factory=_factory_for(httpx.MockTransport(handler))
        )

        result = await service.post_usage(
            shop_domain="example.myshopify.com",
            amount_cents=5,
            description="1 message above cap",
            idempotency_key="k1",
        )
        assert result.recorded is False
        assert result.capped is True
        assert result.reason == "cap_reached"


# ──────────────────────────────────────────────────────────────────────
# Configuration errors
# ──────────────────────────────────────────────────────────────────────


class TestConfigErrors:
    @pytest.mark.asyncio
    async def test_missing_shopify_app_url_raises_relay_config_error(self, monkeypatch):
        _patch_settings(monkeypatch, base_url="", key="some-key")
        service = UsageRelayService()

        with pytest.raises(RelayConfigError, match="shopify_app_url"):
            await service.post_usage(
                shop_domain="example.myshopify.com",
                amount_cents=5,
                description="x",
                idempotency_key="k",
            )

    @pytest.mark.asyncio
    async def test_missing_internal_key_raises_relay_config_error(self, monkeypatch):
        _patch_settings(monkeypatch, base_url="https://shopify.test", key="")
        service = UsageRelayService()

        with pytest.raises(RelayConfigError, match="shopify_internal_key"):
            await service.post_usage(
                shop_domain="example.myshopify.com",
                amount_cents=5,
                description="x",
                idempotency_key="k",
            )

    @pytest.mark.asyncio
    async def test_401_response_raises_relay_config_error(self, monkeypatch):
        _patch_settings(monkeypatch, base_url="https://shopify.test", key="wrong-key")

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid_internal_key"})

        service = UsageRelayService(
            client_factory=_factory_for(httpx.MockTransport(handler))
        )

        with pytest.raises(RelayConfigError, match="X-Internal-Key"):
            await service.post_usage(
                shop_domain="example.myshopify.com",
                amount_cents=5,
                description="x",
                idempotency_key="k",
            )


# ──────────────────────────────────────────────────────────────────────
# Transient + programmer errors
# ──────────────────────────────────────────────────────────────────────


class TestTransientErrors:
    @pytest.mark.asyncio
    async def test_422_response_raises_relay_invalid_payload(self, monkeypatch):
        _patch_settings(monkeypatch, base_url="https://shopify.test", key="k")

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422,
                json={
                    "error": "invalid_body",
                    "detail": "amount_cents must be positive",
                },
            )

        service = UsageRelayService(
            client_factory=_factory_for(httpx.MockTransport(handler))
        )

        with pytest.raises(RelayInvalidPayload, match="amount_cents"):
            await service.post_usage(
                shop_domain="example.myshopify.com",
                amount_cents=-1,
                description="x",
                idempotency_key="k",
            )

    @pytest.mark.asyncio
    async def test_500_response_raises_relay_unavailable(self, monkeypatch):
        _patch_settings(monkeypatch, base_url="https://shopify.test", key="k")

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="upstream is down")

        service = UsageRelayService(
            client_factory=_factory_for(httpx.MockTransport(handler))
        )

        with pytest.raises(RelayUnavailable, match="http_500"):
            await service.post_usage(
                shop_domain="example.myshopify.com",
                amount_cents=5,
                description="x",
                idempotency_key="k",
            )

    @pytest.mark.asyncio
    async def test_network_error_raises_relay_unavailable(self, monkeypatch):
        _patch_settings(monkeypatch, base_url="https://shopify.test", key="k")

        async def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        service = UsageRelayService(
            client_factory=_factory_for(httpx.MockTransport(handler))
        )

        with pytest.raises(RelayUnavailable, match="connection refused"):
            await service.post_usage(
                shop_domain="example.myshopify.com",
                amount_cents=5,
                description="x",
                idempotency_key="k",
            )

    @pytest.mark.asyncio
    async def test_timeout_raises_relay_unavailable(self, monkeypatch):
        _patch_settings(monkeypatch, base_url="https://shopify.test", key="k")

        async def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("request timed out")

        service = UsageRelayService(
            client_factory=_factory_for(httpx.MockTransport(handler))
        )

        with pytest.raises(RelayUnavailable, match="timed out"):
            await service.post_usage(
                shop_domain="example.myshopify.com",
                amount_cents=5,
                description="x",
                idempotency_key="k",
            )

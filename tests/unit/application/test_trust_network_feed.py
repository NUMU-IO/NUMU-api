"""Trust Network contribution-feed client — gating, payload, idempotency, fail-open."""

from __future__ import annotations

import asyncio
import json

import httpx

from src.application.services.trust_network_feed import (
    feed_config,
    post_outcome,
    send_network_outcome,
)


def test_feed_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRUST_NETWORK_FEED_ENABLED", raising=False)
    assert feed_config()["enabled"] is False
    # Disabled → no-op, returns False, never raises.
    out = asyncio.run(
        send_network_outcome(phone_hash="abc123", event_type="rto", dedup_key="s:1:rto")
    )
    assert out is False


def _enable(monkeypatch, handler):
    monkeypatch.setenv("TRUST_NETWORK_FEED_ENABLED", "true")
    monkeypatch.setenv("TRUST_NETWORK_URL", "http://trust-network:8000")
    monkeypatch.setenv("TRUST_NETWORK_API_KEY", "sk_live_test")
    return httpx.MockTransport(handler)


def test_feed_sends_correct_payload_and_headers(monkeypatch):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["idem"] = request.headers.get("Idempotency-Key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"recorded": True, "duplicate": False})

    transport = _enable(monkeypatch, handler)
    out = asyncio.run(
        send_network_outcome(
            phone_hash="tok_abc",
            event_type="rto",
            dedup_key="store:order:rto",
            transport=transport,
        )
    )
    assert out is True
    assert captured["url"] == "http://trust-network:8000/v1/events"
    assert captured["auth"] == "Bearer sk_live_test"
    assert captured["idem"] == "store:order:rto"  # dedup_key IS the idempotency key
    assert captured["body"] == {
        "buyer_token": "tok_abc",  # phone_hash sent as the token (no raw PII)
        "event_type": "rto",
        "dedup_key": "store:order:rto",
    }


def test_feed_non_2xx_returns_false(monkeypatch):
    transport = _enable(
        monkeypatch, lambda r: httpx.Response(401, json={"detail": "nope"})
    )
    out = asyncio.run(
        send_network_outcome(
            phone_hash="t", event_type="delivery", dedup_key="d1", transport=transport
        )
    )
    assert out is False


def test_feed_fail_open_on_transport_error(monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    transport = _enable(monkeypatch, boom)
    out = asyncio.run(
        send_network_outcome(
            phone_hash="t", event_type="delivery", dedup_key="d1", transport=transport
        )
    )
    assert out is False  # never raises — NUMU's own write is unaffected


def test_post_outcome_guards_missing_fields():
    async def _run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        ) as client:
            # missing url / phone_hash / dedup_key → False without a request
            assert (
                await post_outcome(
                    client,
                    url="",
                    api_key="k",
                    phone_hash="t",
                    event_type="rto",
                    dedup_key="d",
                )
                is False
            )
            assert (
                await post_outcome(
                    client,
                    url="http://x",
                    api_key="k",
                    phone_hash="",
                    event_type="rto",
                    dedup_key="d",
                )
                is False
            )
            assert (
                await post_outcome(
                    client,
                    url="http://x",
                    api_key="k",
                    phone_hash="t",
                    event_type="rto",
                    dedup_key="",
                )
                is False
            )

    asyncio.run(_run())

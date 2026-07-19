"""Storefront TN gate service — env gating, dual-mode mapping, fail-open."""

from __future__ import annotations

import asyncio

import httpx

from src.application.services.trust_network_storefront import (
    fetch_network_intelligence,
    storefront_tn_config,
)

HASH = "a" * 64


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRUST_NETWORK_STOREFRONT_ENABLED", raising=False)
    assert storefront_tn_config()["enabled"] is False
    assert asyncio.run(fetch_network_intelligence(phone_hash=HASH)) is None


def _enable(monkeypatch):
    monkeypatch.setenv("TRUST_NETWORK_STOREFRONT_ENABLED", "true")
    monkeypatch.setenv("TRUST_NETWORK_URL", "http://trust-network:8000")
    monkeypatch.setenv("TRUST_NETWORK_API_KEY", "sk_test")


def test_reputation_mode_when_total_unknown(monkeypatch):
    # The storefront gate runs before totals exist → the reputation read-model
    # (semantic parity with the local lookup; no fabricated order inputs).
    _enable(monkeypatch)
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "token_prefix": HASH[:8],
                "known": True,
                "network_risk_score": 71,
                "confidence": "high",
                "label": "risky (2 stores)",
                "contributing_store_count": 2,
            },
        )

    out = asyncio.run(
        fetch_network_intelligence(
            phone_hash=HASH, transport=httpx.MockTransport(handler)
        )
    )
    assert out == (71, "high", "risky (2 stores)")
    assert captured["method"] == "GET"
    assert captured["path"] == f"/v1/reputation/buyer/{HASH}"
    assert captured["auth"] == "Bearer sk_test"


def test_decision_mode_with_total(monkeypatch):
    # With a known order total the full /v1/decisions engages (total_cents is
    # REQUIRED by the TN's DecisionRequest — the live 422 regression).
    _enable(monkeypatch)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["path"] = request.url.path
        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "risk_score": 64,
                "confidence": "high",
                "network_label": "risky (2 stores)",
                "network_source": "graph",
            },
        )

    out = asyncio.run(
        fetch_network_intelligence(
            phone_hash=HASH,
            total_cents=80000,
            transport=httpx.MockTransport(handler),
        )
    )
    assert out == (64, "high", "risky (2 stores)")
    assert captured["path"] == "/v1/decisions"
    assert captured["body"] == {
        "total_cents": 80000,
        "payment_method": "cod",
        "buyer_token": HASH,
    }


def test_non_200_and_malformed_fail_open(monkeypatch):
    _enable(monkeypatch)
    for response in (
        httpx.Response(500, json={"detail": "boom"}),
        httpx.Response(422, json={"detail": "missing field"}),
        httpx.Response(200, json={"network_risk_score": "not-an-int"}),
        httpx.Response(200, json={}),
    ):
        out = asyncio.run(
            fetch_network_intelligence(
                phone_hash=HASH,
                transport=httpx.MockTransport(lambda r, resp=response: resp),
            )
        )
        assert out is None


def test_transport_error_fails_open(monkeypatch):
    _enable(monkeypatch)

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    out = asyncio.run(
        fetch_network_intelligence(
            phone_hash=HASH, transport=httpx.MockTransport(handler)
        )
    )
    assert out is None

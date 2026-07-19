"""Storefront TN gate service — env gating, mapping, fail-open."""

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


def test_maps_decision_to_intelligence_tuple(monkeypatch):
    _enable(monkeypatch)
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "risk_score": 71,
                "confidence": "high",
                "network_label": "risky (2 stores)",
                "network_source": "graph",
                "decided_by": "deterministic",
            },
        )

    out = asyncio.run(
        fetch_network_intelligence(
            phone_hash=HASH, transport=httpx.MockTransport(handler)
        )
    )
    assert out == (71, "high", "risky (2 stores)")
    assert captured["auth"] == "Bearer sk_test"
    assert captured["path"] == "/v1/decisions"


def test_non_200_and_malformed_fail_open(monkeypatch):
    _enable(monkeypatch)
    for response in (
        httpx.Response(500, json={"detail": "boom"}),
        httpx.Response(200, json={"risk_score": "not-an-int"}),
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

"""Trust Network shadow client — request building, gating, and comparison."""

from __future__ import annotations

import asyncio

import httpx

from src.application.services.trust_network_shadow import (
    build_shadow_request,
    compare_with_trust_network,
    network_factors_to_numu,
    resolve_authoritative_score,
    shadow_config,
)

DI = {
    "schema_version": 1,
    "total_cents": 80000,
    "payment_method": "cod",
    "avg_order_cents": 80000,
    "customer_total_orders": 3,
    "customer_cancellation_rate": 0.1,
    "network_score": 20,
    "network_label": "trusted",
    "created_at": "2026-06-29T00:00:00+00:00",
    "product_tags": ["electronics"],
    "address_length": 40,
    "phone_state": "valid",
}


def test_build_shadow_request_uses_determinants_no_pii():
    req = build_shadow_request(DI)
    assert req["network_score"] == 20
    assert req["network_label"] == "trusted"
    assert req["address"] == "x" * 40  # synth, not a real address
    assert req["phone"] == "+201012345678"  # synth valid number
    assert req["customer_total_orders"] == 3
    # determinants themselves never travel
    assert "address_length" not in req
    assert "phone_state" not in req


def test_build_shadow_request_drops_none_and_handles_empty():
    assert build_shadow_request({"total_cents": 1000}) == {"total_cents": 1000}
    assert build_shadow_request(None) == {}


def test_build_shadow_request_phone_states():
    assert "phone" not in build_shadow_request({
        "total_cents": 1,
        "phone_state": "absent",
    })
    assert (
        build_shadow_request({"total_cents": 1, "phone_state": "invalid"})["phone"]
        == "0"
    )


def test_shadow_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRUST_NETWORK_SHADOW_ENABLED", raising=False)
    assert shadow_config()["enabled"] is False
    assert (
        asyncio.run(compare_with_trust_network(decision_inputs=DI, numu_risk_score=50))
        is None
    )


def _enable(monkeypatch, handler):
    monkeypatch.setenv("TRUST_NETWORK_SHADOW_ENABLED", "true")
    monkeypatch.setenv("TRUST_NETWORK_URL", "http://trust-network:8000")
    monkeypatch.setenv("TRUST_NETWORK_API_KEY", "sk_test")
    return httpx.MockTransport(handler)


def test_shadow_match(monkeypatch):
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["idem"] = request.headers.get("Idempotency-Key")
        return httpx.Response(200, json={"risk_score": 50})

    transport = _enable(monkeypatch, handler)
    out = asyncio.run(
        compare_with_trust_network(
            decision_inputs=DI, numu_risk_score=50, order_ref="abc", transport=transport
        )
    )
    assert out is not None
    assert out["match"] is True
    assert out["drift"] == 0
    assert out["service_risk_score"] == 50
    assert captured["auth"] == "Bearer sk_test"
    assert captured["idem"] == "shadow-abc"


def test_shadow_drift(monkeypatch):
    transport = _enable(
        monkeypatch, lambda r: httpx.Response(200, json={"risk_score": 47})
    )
    out = asyncio.run(
        compare_with_trust_network(
            decision_inputs=DI, numu_risk_score=50, transport=transport
        )
    )
    assert out is not None
    assert out["match"] is False
    assert out["drift"] == 3


def test_shadow_captures_network_factors(monkeypatch):
    # The network's factor breakdown flows through for persistence under cutover.
    factors = [
        {
            "factor": "network_reputation",
            "score": 90.0,
            "weight": 0.4,
            "reason": "3 RTOs across 2 stores",
        }
    ]
    transport = _enable(
        monkeypatch,
        lambda r: httpx.Response(200, json={"risk_score": 80, "factors": factors}),
    )
    out = asyncio.run(
        compare_with_trust_network(
            decision_inputs=DI, numu_risk_score=50, transport=transport
        )
    )
    assert out["service_factors"] == factors


def test_shadow_missing_factors_defaults_empty(monkeypatch):
    transport = _enable(
        monkeypatch, lambda r: httpx.Response(200, json={"risk_score": 50})
    )
    out = asyncio.run(
        compare_with_trust_network(
            decision_inputs=DI, numu_risk_score=50, transport=transport
        )
    )
    assert out["service_factors"] == []


def test_shadow_non_200_returns_none(monkeypatch):
    transport = _enable(
        monkeypatch, lambda r: httpx.Response(500, json={"detail": "boom"})
    )
    out = asyncio.run(
        compare_with_trust_network(
            decision_inputs=DI, numu_risk_score=50, transport=transport
        )
    )
    assert out is None


# ── P1-7 cutover: resolve_authoritative_score ───────────────────────────────


def test_resolve_cutover_off_uses_numu():
    # Flag off: NUMU's score wins even when the network score differs wildly.
    assert resolve_authoritative_score(
        numu_risk_score=50,
        tn_comparison={"service_risk_score": 80, "match": False, "drift": 30},
        cutover_enabled=False,
    ) == (50, "numu")


def test_resolve_cutover_on_uses_network():
    assert resolve_authoritative_score(
        numu_risk_score=50,
        tn_comparison={"service_risk_score": 80},
        cutover_enabled=True,
    ) == (80, "network")


def test_resolve_fails_open_when_network_silent():
    # Cutover on but the network didn't answer (None) → NUMU score, never raises.
    assert resolve_authoritative_score(
        numu_risk_score=42,
        tn_comparison=None,
        cutover_enabled=True,
    ) == (42, "numu")


def test_resolve_ignores_non_numeric_service_score():
    # Malformed/absent service score → fail open to NUMU (bool is not numeric).
    for bad in (None, "80", True):
        assert resolve_authoritative_score(
            numu_risk_score=42,
            tn_comparison={"service_risk_score": bad},
            cutover_enabled=True,
        ) == (42, "numu")


def test_resolve_coerces_float_service_score():
    assert resolve_authoritative_score(
        numu_risk_score=10,
        tn_comparison={"service_risk_score": 73.0},
        cutover_enabled=True,
    ) == (73, "network")


# ── P1-7 cutover: network_factors_to_numu (factor-shape mapping) ─────────────


def test_network_factors_remaps_keys_to_numu_shape():
    # FactorOut {factor, score, weight, reason} -> NUMU {name, score, weight, detail}
    out = network_factors_to_numu([
        {
            "factor": "network_reputation",
            "score": 90.0,
            "weight": 0.4,
            "reason": "risky (2 stores)",
        }
    ])
    assert out == [
        {
            "name": "network_reputation",
            "score": 90.0,
            "weight": 0.4,
            "detail": "risky (2 stores)",
        }
    ]


def test_network_factors_handles_missing_and_malformed():
    assert network_factors_to_numu(None) == []
    assert network_factors_to_numu([]) == []
    # Non-dict entries are skipped; a partial dict maps present keys, None else.
    out = network_factors_to_numu(["nope", {"factor": "x"}])
    assert out == [{"name": "x", "score": None, "weight": None, "detail": None}]

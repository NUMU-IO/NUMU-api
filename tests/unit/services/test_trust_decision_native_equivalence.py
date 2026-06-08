"""Snapshot-pin: the canonical FSM matches the live native cod_trust block
decision across a representative input matrix (Phase C cutover gate / R2).

This is the equivalence proof the strangler requires before the native
checkout is cut over to consume the FSM: across every combination of network
score, confidence, and merchant cod_trust settings, ``native_block_equivalent
(decide(...))`` must equal the real ``check_customer_trust`` block decision.
It doubles as a due-diligence artifact ("the new engine provably matches the
old one").
"""

from __future__ import annotations

import pytest

from src.application.services import cod_trust_service
from src.application.services.cod_trust_service import check_customer_trust
from src.application.services.trust_decision_service import (
    DecisionInputs,
    decide,
    native_block_equivalent,
)


@pytest.fixture(autouse=True)
def _stub_salt(monkeypatch):
    """check_customer_trust hashes the phone; give it a salt so it doesn't
    short-circuit to the 'no_phone' allow path."""

    class _S:
        platform_secret_salt = "test-salt"

    monkeypatch.setattr(
        "src.application.services.network_reputation_service.get_settings",
        lambda: _S(),
    )


@pytest.mark.parametrize("score", [10, 40, 55, 70, 71, 85, 100])
@pytest.mark.parametrize("confidence", ["low", "medium", "high"])
@pytest.mark.parametrize(
    ("action", "threshold", "min_conf"),
    [
        ("block", 70, "medium"),
        ("warn", 70, "medium"),
        ("block", 50, "low"),
        ("block", 90, "high"),
    ],
)
@pytest.mark.asyncio
async def test_fsm_matches_native_block_decision(
    monkeypatch, score, confidence, action, threshold, min_conf
):
    # Force a controlled network reputation so the live decision is a pure
    # function of (score, confidence, settings) — no DB/Redis needed.
    async def _fake_lookup(phone_hash, repo):
        return score, confidence, "controlled"

    monkeypatch.setattr(cod_trust_service, "lookup_network_reputation", _fake_lookup)

    store_settings = {
        "cod_trust": {
            "enabled": True,
            "threshold": threshold,
            "action": action,
            "min_confidence": min_conf,
        }
    }

    live = await check_customer_trust(
        phone="+201001234567",
        store_settings=store_settings,
        network_repo=object(),
        location=None,
    )

    fsm_state = decide(
        DecisionInputs(
            risk_score=live.score or 0,
            confidence=live.confidence or "low",
            block_enabled=(action == "block"),
            block_threshold=threshold,
            min_confidence_to_act=min_conf,
        )
    )

    assert native_block_equivalent(fsm_state) == (not live.allowed), (
        f"FSM/native disagree at score={score} confidence={confidence} "
        f"action={action} threshold={threshold} min_conf={min_conf}: "
        f"fsm={fsm_state.value} live_allowed={live.allowed}"
    )


@pytest.mark.asyncio
async def test_disabled_cod_trust_allows_and_fsm_agrees(monkeypatch):
    """When the merchant hasn't enabled cod_trust the native path allows
    unconditionally; the FSM (block disabled) must also not block."""

    async def _fake_lookup(phone_hash, repo):
        return 100, "high", "controlled"

    monkeypatch.setattr(cod_trust_service, "lookup_network_reputation", _fake_lookup)

    live = await check_customer_trust(
        phone="+201001234567",
        store_settings={"cod_trust": {"enabled": False}},
        network_repo=object(),
        location=None,
    )
    assert live.allowed is True

    fsm_state = decide(
        DecisionInputs(risk_score=100, confidence="high", block_enabled=False)
    )
    assert native_block_equivalent(fsm_state) is False

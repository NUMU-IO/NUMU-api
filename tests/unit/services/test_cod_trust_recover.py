"""Unit tests for the COD-trust "recover" action — the second merchant flow.

block   → reject high-risk COD (allowed=False)
warn    → allow + log (allowed=True, recover=False)
recover → allow the COD order + flag it so the caller fires a WhatsApp
          payment-link offer to convert it to prepaid (allowed=True, recover=True)
"""

from __future__ import annotations

import pytest

from src.application.services import cod_trust_service
from src.application.services.cod_trust_service import check_customer_trust


@pytest.fixture(autouse=True)
def _stub_salt(monkeypatch):
    class _S:
        platform_secret_salt = "test-salt"

    monkeypatch.setattr(
        "src.application.services.network_reputation_service.get_settings",
        lambda: _S(),
    )


def _settings(action, threshold=70, min_conf="medium"):
    return {
        "cod_trust": {
            "enabled": True,
            "threshold": threshold,
            "action": action,
            "min_confidence": min_conf,
        }
    }


async def _decide(monkeypatch, *, score, confidence, action, min_conf="medium"):
    async def _fake_lookup(phone_hash, repo):
        return score, confidence, "controlled"

    monkeypatch.setattr(cod_trust_service, "lookup_network_reputation", _fake_lookup)
    return await check_customer_trust(
        phone="+201001234567",
        store_settings=_settings(action, min_conf=min_conf),
        network_repo=object(),
        location=None,
    )


class TestRecoverAction:
    @pytest.mark.asyncio
    async def test_recover_high_risk_allows_and_flags_recovery(self, monkeypatch):
        d = await _decide(monkeypatch, score=85, confidence="high", action="recover")
        assert d.allowed is True
        assert d.recover is True
        assert d.reason == "recover_high_risk"

    @pytest.mark.asyncio
    async def test_block_high_risk_still_blocks(self, monkeypatch):
        d = await _decide(monkeypatch, score=85, confidence="high", action="block")
        assert d.allowed is False
        assert d.recover is False

    @pytest.mark.asyncio
    async def test_warn_high_risk_allows_without_recovery(self, monkeypatch):
        d = await _decide(monkeypatch, score=85, confidence="high", action="warn")
        assert d.allowed is True
        assert d.recover is False
        assert d.reason == "warned_high_risk"

    @pytest.mark.asyncio
    async def test_recover_low_risk_does_not_recover(self, monkeypatch):
        # Below threshold — a good buyer, nothing to convert.
        d = await _decide(monkeypatch, score=10, confidence="high", action="recover")
        assert d.allowed is True
        assert d.recover is False

    @pytest.mark.asyncio
    async def test_recover_low_confidence_does_not_recover(self, monkeypatch):
        # Thin data — we neither block nor push a recovery offer.
        d = await _decide(
            monkeypatch, score=85, confidence="low", action="recover", min_conf="medium"
        )
        assert d.allowed is True
        assert d.recover is False
        assert d.reason == "low_confidence"

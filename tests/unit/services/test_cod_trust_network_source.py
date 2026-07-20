"""COD trust gate × Trust Network intelligence source (full-partner read path).

When the TN answers, its (score, confidence, label) feed the gate and the
decision carries a provenance factor; when it can't, the local lookup runs
exactly as before. Flag-off behaviour is pinned by the entire pre-existing
``test_cod_trust_service.py`` suite (fetch returns None with no env set).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.application.services.cod_trust_service import check_customer_trust

_SETTINGS = {
    "cod_trust": {
        "enabled": True,
        "threshold": 70,
        "min_confidence": "medium",
        "action": "block",
    }
}


class _RepRow:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _AbuserRepo:
    """Local-graph abuser: high score + high confidence when consulted."""

    async def get_by_phone_hash(self, _phone_hash):
        return _RepRow(
            total_network_orders=10,
            total_network_rtos=9,
            total_successful_deliveries=1,
            total_refunds=0,
            contributing_store_count=3,
        )


@pytest.fixture(autouse=True)
def _stub_phone_salt(monkeypatch):
    class _S:
        platform_secret_salt = "test-salt"

    monkeypatch.setattr(
        "src.application.services.network_reputation_service.get_settings",
        lambda: _S(),
    )


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    class _BadRedis:
        def __init__(self, *a, **kw):
            pass

        async def get(self, _key):
            raise RuntimeError("no redis")

        async def set(self, *_a, **_kw):
            raise RuntimeError("no redis")

        async def delete(self, _key):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(
        "src.infrastructure.cache.redis_cache.RedisCacheService", _BadRedis
    )


@pytest.mark.asyncio
async def test_network_intelligence_feeds_the_gate(monkeypatch):
    async def fake_fetch(*, phone_hash, total_cents=None):
        return (90, "high", "serial_abuser (8 stores)")

    monkeypatch.setattr(
        "src.application.services.trust_network_storefront.fetch_network_intelligence",
        fake_fetch,
    )
    decision = await check_customer_trust(
        phone="+201001234567",
        store_settings=_SETTINGS,
        network_repo=_AbuserRepo(),
    )
    assert decision.allowed is False
    assert decision.reason == "blocked_high_risk"
    assert decision.score == 90
    assert decision.label == "serial_abuser (8 stores)"
    # Provenance factor marks the network as the intelligence source.
    assert any(f["code"] == "network_intelligence" for f in decision.factors)


@pytest.mark.asyncio
async def test_tn_silence_falls_back_to_local(monkeypatch):
    async def fake_fetch(*, phone_hash, total_cents=None):
        return None  # disabled / timeout / error — all collapse to None

    monkeypatch.setattr(
        "src.application.services.trust_network_storefront.fetch_network_intelligence",
        fake_fetch,
    )
    decision = await check_customer_trust(
        phone="+201001234567",
        store_settings=_SETTINGS,
        network_repo=_AbuserRepo(),
    )
    # The local abuser record still blocks — behaviour identical to before.
    assert decision.allowed is False
    assert decision.reason == "blocked_high_risk"
    # No provenance factor — the local path fed the gate.
    assert not any(f["code"] == "network_intelligence" for f in decision.factors)


@pytest.mark.asyncio
async def test_tn_raising_is_contained_and_falls_back(monkeypatch):
    async def fake_fetch(*, phone_hash, total_cents=None):
        raise RuntimeError("unexpected SDK explosion")

    monkeypatch.setattr(
        "src.application.services.trust_network_storefront.fetch_network_intelligence",
        fake_fetch,
    )
    decision = await check_customer_trust(
        phone="+201001234567",
        store_settings=_SETTINGS,
        network_repo=_AbuserRepo(),
    )
    assert decision.allowed is False  # local abuser path still decided
    assert decision.reason == "blocked_high_risk"

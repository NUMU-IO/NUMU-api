"""Unit tests for the per-store COD trust stats + phone-lookup endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.v1.routes.stores.cod_trust_decisions import (
    get_cod_trust_stats,
    lookup_cod_trust_phone,
)


class _Result:
    def __init__(self, all_rows=None, one_val=None):
        self._all = all_rows or []
        self._one = one_val

    def all(self):
        return self._all

    def one(self):
        return self._one


@pytest.mark.asyncio
async def test_stats_current_and_previous_windows():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            # current window: group-by, then recovered (count, sum cents)
            _Result(
                all_rows=[
                    ("blocked_high_risk", 3),
                    ("warned_high_risk", 2),
                    ("below_threshold", 10),
                    ("recover_high_risk", 1),
                ]
            ),
            _Result(one_val=(4, 100000)),
            # previous window
            _Result(all_rows=[("below_threshold", 5)]),
            _Result(one_val=(1, 25000)),
        ]
    )
    store = SimpleNamespace(id=uuid4())

    resp = await get_cod_trust_stats(store, session, period_days=30)

    assert resp.data.period_days == 30
    c = resp.data.current
    assert c.screened == 16  # 3+2+10+1
    assert c.blocked == 3
    assert c.warned == 2
    assert c.high_risk == 6  # blocked + warned + recover-flagged(1)
    assert c.recovered == 4
    assert c.recovered_value == 100000
    p = resp.data.previous
    assert p.screened == 5
    assert p.recovered == 1
    assert p.recovered_value == 25000


@pytest.mark.asyncio
async def test_stats_empty_store_all_zero():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _Result(all_rows=[]),
            _Result(one_val=(0, 0)),
            _Result(all_rows=[]),
            _Result(one_val=(0, 0)),
        ]
    )
    resp = await get_cod_trust_stats(
        SimpleNamespace(id=uuid4()), session, period_days=7
    )
    assert resp.data.current.screened == 0
    assert resp.data.current.recovered_value == 0
    assert resp.data.previous.high_risk == 0


@pytest.mark.asyncio
async def test_lookup_known_abuser(monkeypatch):
    async def _lookup(_ph, _repo):
        return (90, "high", "serial_abuser")

    monkeypatch.setattr(
        "src.application.services.network_reputation_service.lookup_network_reputation",
        _lookup,
    )
    monkeypatch.setattr(
        "src.application.services.network_reputation_service.extract_phone_hash_from_string",
        lambda _p: "deadbeef",
    )

    resp = await lookup_cod_trust_phone(
        SimpleNamespace(id=uuid4()), AsyncMock(), phone="+201001234567"
    )
    assert resp.data.known is True
    assert resp.data.label == "serial_abuser"
    assert resp.data.score == 90
    assert resp.data.phone_last4 == "4567"


@pytest.mark.asyncio
async def test_lookup_new_to_network(monkeypatch):
    async def _lookup(_ph, _repo):
        return (55, "low", "new_to_network")

    monkeypatch.setattr(
        "src.application.services.network_reputation_service.lookup_network_reputation",
        _lookup,
    )
    monkeypatch.setattr(
        "src.application.services.network_reputation_service.extract_phone_hash_from_string",
        lambda _p: None,
    )

    resp = await lookup_cod_trust_phone(
        SimpleNamespace(id=uuid4()), AsyncMock(), phone="01000000000"
    )
    assert resp.data.known is False
    assert resp.data.phone_last4 == "0000"

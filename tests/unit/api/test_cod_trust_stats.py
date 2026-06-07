"""Unit tests for the per-store COD trust stats endpoint aggregation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.v1.routes.stores.cod_trust_decisions import get_cod_trust_stats


class _Result:
    def __init__(self, all_rows=None, scalar_val=None):
        self._all = all_rows or []
        self._scalar = scalar_val

    def all(self):
        return self._all

    def scalar(self):
        return self._scalar


@pytest.mark.asyncio
async def test_stats_aggregate_by_action_and_recovered():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _Result(
                all_rows=[
                    ("blocked_high_risk", 3),
                    ("warned_high_risk", 2),
                    ("below_threshold", 10),
                    ("recover_high_risk", 1),
                ]
            ),
            _Result(scalar_val=4),  # recovered (cod_recovered orders)
        ]
    )
    store = SimpleNamespace(id=uuid4())

    resp = await get_cod_trust_stats(store, session, period_days=30)

    assert resp.data.period_days == 30
    assert resp.data.screened == 16  # 3+2+10+1
    assert resp.data.blocked == 3
    assert resp.data.warned == 2
    assert resp.data.high_risk == 6  # blocked + warned + recover-flagged(1)
    assert resp.data.recovered == 4


@pytest.mark.asyncio
async def test_stats_empty_store_is_all_zero():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_Result(all_rows=[]), _Result(scalar_val=None)]
    )
    store = SimpleNamespace(id=uuid4())

    resp = await get_cod_trust_stats(store, session, period_days=7)

    assert resp.data.period_days == 7
    assert resp.data.screened == 0
    assert resp.data.high_risk == 0
    assert resp.data.recovered == 0

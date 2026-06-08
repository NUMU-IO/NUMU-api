"""Unit tests for DB-level network-write idempotency (P1-2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.services.network_reputation_service import write_network_event
from src.infrastructure.repositories.shopify_repository import (
    NetworkReputationRepository,
)


@pytest.fixture(autouse=True)
def _stub_redis(monkeypatch):
    """write_network_event invalidates Redis at the end; fake it."""

    class _R:
        def __init__(self, *a, **k):
            pass

        async def get(self, *_):
            return None

        async def set(self, *a, **k):
            return None

        async def delete(self, *_):
            return None

        async def close(self):
            return None

    monkeypatch.setattr("src.infrastructure.cache.redis_cache.RedisCacheService", _R)


class TestClaimContribution:
    @pytest.mark.asyncio
    async def test_keyed_conflict_returns_false(self):
        """A duplicate dedup_key (ON CONFLICT DO NOTHING → 0 rows) is skipped."""
        repo = NetworkReputationRepository(AsyncMock())
        res = MagicMock()
        res.rowcount = 0
        repo.session.execute = AsyncMock(return_value=res)
        claimed = await repo._claim_contribution(
            phone_hash="h", store_id=uuid4(), event_type="rto", dedup_key="k"
        )
        assert claimed is False

    @pytest.mark.asyncio
    async def test_keyed_insert_returns_true(self):
        repo = NetworkReputationRepository(AsyncMock())
        res = MagicMock()
        res.rowcount = 1
        repo.session.execute = AsyncMock(return_value=res)
        claimed = await repo._claim_contribution(
            phone_hash="h", store_id=uuid4(), event_type="rto", dedup_key="k"
        )
        assert claimed is True

    @pytest.mark.asyncio
    async def test_unkeyed_appends_and_returns_true(self):
        """No key → legacy append-only behaviour (always counts)."""
        session = AsyncMock()
        session.add = MagicMock()
        repo = NetworkReputationRepository(session)
        claimed = await repo._claim_contribution(
            phone_hash="h", store_id=uuid4(), event_type="rto", dedup_key=None
        )
        assert claimed is True
        session.add.assert_called_once()


class TestWriteNetworkEventForwardsDedupKey:
    @pytest.mark.asyncio
    async def test_rto_forwards_dedup_key(self):
        repo = AsyncMock()
        await write_network_event(
            phone_hash="h",
            store_id=uuid4(),
            event_type="rto",
            network_repo=repo,
            dedup_key="store:order:rto",
        )
        repo.record_event.assert_awaited_once()
        assert repo.record_event.await_args.kwargs["dedup_key"] == "store:order:rto"

    @pytest.mark.asyncio
    async def test_order_forwards_dedup_key(self):
        repo = AsyncMock()
        await write_network_event(
            phone_hash="h",
            store_id=uuid4(),
            event_type="order",
            network_repo=repo,
            dedup_key="store:order:order",
        )
        repo.upsert_order.assert_awaited_once()
        assert repo.upsert_order.await_args.kwargs["dedup_key"] == "store:order:order"

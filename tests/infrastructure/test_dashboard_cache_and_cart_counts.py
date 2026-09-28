"""Dashboard read-through cache and the SQL cart-abandonment counts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.api.v1.routes.stores.dashboard import dashboard_cache_key
from src.infrastructure.cache import redis_cache
from src.infrastructure.database.models.tenant.funnel_event import FunnelEventModel
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)


@pytest.mark.asyncio
async def test_cached_json_builds_once_then_serves_the_cache(_dashboard_cache):
    calls = []

    async def build():
        calls.append(1)
        return {"total": 5}

    assert await redis_cache.cached_json("k", 60, build) == {"total": 5}
    assert await redis_cache.cached_json("k", 60, build) == {"total": 5}
    assert len(calls) == 1


def test_cache_key_is_scoped_by_store():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert dashboard_cache_key(a, "stats", "x") != dashboard_cache_key(b, "stats", "x")
    assert str(a) in dashboard_cache_key(a, "stats", "x")


@pytest.mark.asyncio
async def test_cart_session_counts(test_session):
    store_id = uuid.uuid4()
    now = datetime.now(UTC)

    def event(fp, step, when=now):
        return FunnelEventModel(
            id=uuid.uuid4(),
            tenant_id=store_id,
            store_id=store_id,
            session_fingerprint=fp,
            step=step,
            created_at=when,
        )

    test_session.add_all([
        event("a", "page_view"),
        event("a", "add_to_cart"),
        event("a", "add_to_cart"),
        event("a", "order_completed"),
        event("b", "add_to_cart"),
        event("c", "order_completed"),  # ordered without a tracked cart
        event("d", "add_to_cart", now - timedelta(days=40)),  # outside window
        event(None, "add_to_cart"),
    ])
    await test_session.flush()

    carted, converted = await FunnelEventRepository(test_session).cart_session_counts(
        store_id, now - timedelta(days=30), now + timedelta(minutes=1)
    )
    assert (carted, converted) == (2, 1)

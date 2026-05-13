"""Integration test for :class:`StorefrontCache` against a real Redis.

Probes ``TEST_REDIS_URL`` (or ``REDIS_URL``) and ``pytest.skip``s
cleanly when no Redis is reachable. Confirms the cache's SETEX /
pipeline / DELETE calls behave the way the FakeRedis unit tests
asserted, against a real ``redis.asyncio.Redis`` client.

Lessons from Step 04 applied: sync ``def test_*`` driving a private
asyncio event loop, plus flushing a dedicated DB index so the test
never collides with anything else using the same Redis.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from src.infrastructure.cache.storefront_cache import (
    MISSING_SENTINEL,
    StorefrontCache,
)

# Use a high-numbered DB index so we never stomp on dev / prod data.
INTEGRATION_DB = 15


def _probe_url() -> str | None:
    raw = os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL")
    if not raw:
        return None
    # Force the test DB index regardless of what's in the env URL.
    base, _, _ = raw.rpartition("/")
    return f"{base}/{INTEGRATION_DB}" if base.startswith("redis://") else raw


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="module")
def redis_client():
    url = _probe_url()
    if not url:
        pytest.skip("TEST_REDIS_URL / REDIS_URL not set")
    import redis.asyncio as redis_async

    client = redis_async.from_url(url, encoding="utf-8", decode_responses=True)

    async def _ping_and_flush() -> None:
        await client.ping()
        await client.flushdb()

    try:
        _run(_ping_and_flush())
    except Exception as exc:
        _run(client.aclose())
        pytest.skip(f"Redis not reachable: {exc}")
    yield client
    _run(client.flushdb())
    _run(client.aclose())


def test_round_trip_against_real_redis(redis_client) -> None:
    cache = StorefrontCache(redis=redis_client, ttl_seconds=60)
    payload = {
        "id": "11111111-1111-1111-1111-111111111111",
        "subdomain": "integration",
        "custom_domain": "shop.integration.test",
        "name": "Integration",
    }
    _run(cache.set_store(payload))

    assert _run(cache.get_store_by_subdomain("integration")) == payload
    assert _run(cache.get_store_by_id(payload["id"])) == payload
    assert _run(cache.get_store_by_domain("shop.integration.test")) == payload


def test_invalidate_clears_real_redis_keys(redis_client) -> None:
    cache = StorefrontCache(redis=redis_client, ttl_seconds=60)
    payload = {
        "id": "22222222-2222-2222-2222-222222222222",
        "subdomain": "to-invalidate",
        "custom_domain": "to-invalidate.test",
    }
    _run(cache.set_store(payload))
    _run(cache.set_theme(payload["id"], {"primary": "#fff"}))

    _run(
        cache.invalidate_store(
            store_id=payload["id"],
            subdomain=payload["subdomain"],
            custom_domain=payload["custom_domain"],
        )
    )
    _run(cache.invalidate_theme(payload["id"]))

    assert _run(cache.get_store_by_subdomain("to-invalidate")) is None
    assert _run(cache.get_store_by_id(payload["id"])) is None
    assert _run(cache.get_store_by_domain("to-invalidate.test")) is None
    assert _run(cache.get_theme(payload["id"])) is None


def test_negative_cache_sentinel_in_real_redis(redis_client) -> None:
    cache = StorefrontCache(redis=redis_client, ttl_seconds=60, negative_ttl_seconds=5)
    _run(cache.set_store_missing(subdomain="ghost-store"))
    assert _run(cache.get_store_by_subdomain("ghost-store")) == MISSING_SENTINEL

    # And the TTL came back short, not the long positive one
    ttl = _run(redis_client.ttl("store:by_subdomain:ghost-store"))
    assert 0 < ttl <= 5

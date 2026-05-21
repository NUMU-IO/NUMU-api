"""Unit tests for :class:`StorefrontCache`.

Covers the four behaviours the plan §7.1 calls out:

* kill switch (``enabled=False``) bypasses every read and write
* set/get round-trips across all three lookup keys
* invalidation drops by_id + by_subdomain + by_domain
* negative cache returns a distinct ``MISSING_SENTINEL`` so the
  caller can distinguish "we know this is missing" from "we don't
  know either way"
* graceful degradation when Redis raises ``RedisError``

Lessons from Step 04 applied: sync ``def test_*`` functions driving
a private asyncio event loop, no async pytest fixtures — keeps these
out of the project's session-scoped asyncpg pool interaction that
breaks on Windows.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from redis.exceptions import RedisError

from src.infrastructure.cache.storefront_cache import (
    MISSING_SENTINEL,
    StorefrontCache,
)

# ---------------------------------------------------------------------- #
# Test helpers                                                            #
# ---------------------------------------------------------------------- #


class FakeRedis:
    """Minimal in-memory async Redis stand-in.

    Implements just the subset StorefrontCache uses: ``get``, ``setex``,
    ``set``, ``delete``, and ``pipeline`` (as an async context manager).
    TTLs are recorded but not enforced — tests run within microseconds.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self.store[key] = value
        self.ttls[key] = ttl
        return True

    async def set(self, key: str, value: str) -> bool:
        self.store[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                self.ttls.pop(k, None)
                removed += 1
        return removed

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    """Async-context-manager pipeline matching redis.asyncio.Pipeline shape."""

    def __init__(self, parent: FakeRedis) -> None:
        self.parent = parent
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def setex(self, key: str, ttl: int, value: str) -> FakePipeline:
        self._ops.append(("setex", (key, ttl, value)))
        return self

    def set(self, key: str, value: str) -> FakePipeline:
        self._ops.append(("set", (key, value)))
        return self

    def delete(self, *keys: str) -> FakePipeline:
        self._ops.append(("delete", keys))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for op, args in self._ops:
            if op == "setex":
                results.append(await self.parent.setex(*args))
            elif op == "set":
                results.append(await self.parent.set(*args))
            elif op == "delete":
                results.append(await self.parent.delete(*args))
        self._ops.clear()
        return results


class BrokenRedis:
    """Every operation raises ``RedisError`` — exercises graceful degradation."""

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisError("simulated outage")

    async def setex(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisError("simulated outage")

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisError("simulated outage")

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisError("simulated outage")

    def pipeline(self) -> BrokenPipeline:
        return BrokenPipeline()


class BrokenPipeline:
    @asynccontextmanager
    async def _ctx(self):
        yield self

    async def __aenter__(self) -> BrokenPipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def setex(self, *args: Any, **kwargs: Any) -> BrokenPipeline:
        return self

    def set(self, *args: Any, **kwargs: Any) -> BrokenPipeline:
        return self

    def delete(self, *args: Any, **kwargs: Any) -> BrokenPipeline:
        return self

    async def execute(self) -> Any:
        raise RedisError("simulated outage")


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _payload(
    store_id: str = "11111111-1111-1111-1111-111111111111",
    subdomain: str = "demo",
    custom_domain: str | None = None,
) -> dict:
    return {
        "id": store_id,
        "subdomain": subdomain,
        "custom_domain": custom_domain,
        "name": "Demo Store",
    }


# ---------------------------------------------------------------------- #
# Set / get round-trip                                                    #
# ---------------------------------------------------------------------- #


def test_set_store_round_trips_all_three_keys() -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60)

    payload = _payload(
        store_id="abc",
        subdomain="foo",
        custom_domain="shop.example.com",
    )
    _run(cache.set_store(payload))

    assert _run(cache.get_store_by_subdomain("foo")) == payload
    assert _run(cache.get_store_by_id("abc")) == payload
    assert _run(cache.get_store_by_domain("shop.example.com")) == payload
    # By-domain key normalises case
    assert _run(cache.get_store_by_domain("SHOP.EXAMPLE.COM")) == payload


def test_set_store_without_custom_domain_skips_domain_key() -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60)

    _run(cache.set_store(_payload(custom_domain=None)))
    assert "store:by_domain:" not in "".join(redis.store.keys())


# ---------------------------------------------------------------------- #
# Invalidation                                                            #
# ---------------------------------------------------------------------- #


def test_invalidate_store_drops_all_listed_keys() -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60)

    payload = _payload(
        store_id="abc",
        subdomain="foo",
        custom_domain="shop.example.com",
    )
    _run(cache.set_store(payload))

    _run(
        cache.invalidate_store(
            store_id="abc",
            subdomain="foo",
            custom_domain="shop.example.com",
        )
    )

    assert _run(cache.get_store_by_subdomain("foo")) is None
    assert _run(cache.get_store_by_id("abc")) is None
    assert _run(cache.get_store_by_domain("shop.example.com")) is None


def test_invalidate_theme_only_drops_theme_key() -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60)

    _run(cache.set_store(_payload(store_id="abc", subdomain="foo")))
    _run(cache.set_theme("abc", {"primary_color": "#000"}))

    _run(cache.invalidate_theme("abc"))

    # Theme gone, store payload still cached
    assert _run(cache.get_theme("abc")) is None
    assert _run(cache.get_store_by_subdomain("foo")) is not None


# ---------------------------------------------------------------------- #
# Negative caching                                                        #
# ---------------------------------------------------------------------- #


def test_negative_caching_returns_sentinel() -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60, negative_ttl_seconds=10)

    _run(cache.set_store_missing(subdomain="ghost"))
    assert _run(cache.get_store_by_subdomain("ghost")) == MISSING_SENTINEL
    # Negative TTL is shorter than the positive one
    stored_key = "store:by_subdomain:ghost"
    assert redis.ttls[stored_key] == 10


def test_negative_caching_can_target_custom_domain() -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60)

    _run(cache.set_store_missing(custom_domain="ghost.example.com"))
    assert _run(cache.get_store_by_domain("ghost.example.com")) == MISSING_SENTINEL


# ---------------------------------------------------------------------- #
# Kill switch                                                             #
# ---------------------------------------------------------------------- #


def test_kill_switch_disables_reads() -> None:
    redis = FakeRedis()
    # Seed Redis directly so we know there *is* data to find
    redis.store["store:by_subdomain:foo"] = json.dumps(_payload())

    cache = StorefrontCache(redis=redis, ttl_seconds=60, enabled=False)

    assert _run(cache.get_store_by_subdomain("foo")) is None
    assert _run(cache.get_store_by_id("abc")) is None
    assert _run(cache.get_store_by_domain("any.example.com")) is None


def test_kill_switch_disables_writes() -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60, enabled=False)

    _run(cache.set_store(_payload(subdomain="foo")))
    _run(cache.set_store_missing(subdomain="ghost"))
    _run(cache.set_theme("abc", {"primary": "#000"}))

    assert redis.store == {}


def test_kill_switch_disables_invalidation() -> None:
    redis = FakeRedis()
    redis.store["store:by_id:abc"] = json.dumps(_payload())

    cache = StorefrontCache(redis=redis, ttl_seconds=60, enabled=False)

    _run(cache.invalidate_store(store_id="abc"))
    _run(cache.invalidate_theme("abc"))

    # Disabled cache should not touch Redis
    assert "store:by_id:abc" in redis.store


# ---------------------------------------------------------------------- #
# Graceful degradation                                                    #
# ---------------------------------------------------------------------- #


def test_redis_down_get_returns_none() -> None:
    cache = StorefrontCache(redis=BrokenRedis(), ttl_seconds=60)
    # No exception escapes
    assert _run(cache.get_store_by_subdomain("foo")) is None
    assert _run(cache.get_store_by_id("abc")) is None
    assert _run(cache.get_theme("abc")) is None


def test_redis_down_writes_are_swallowed() -> None:
    cache = StorefrontCache(redis=BrokenRedis(), ttl_seconds=60)
    # All must be no-throw
    _run(cache.set_store(_payload()))
    _run(cache.set_store_missing(subdomain="ghost"))
    _run(cache.set_theme("abc", {"x": 1}))
    _run(cache.invalidate_store(store_id="abc", subdomain="foo"))
    _run(cache.invalidate_theme("abc"))


# ---------------------------------------------------------------------- #
# Sanity: get on cold cache returns None                                  #
# ---------------------------------------------------------------------- #


def test_cold_cache_misses_return_none() -> None:
    cache = StorefrontCache(redis=FakeRedis(), ttl_seconds=60)
    assert _run(cache.get_store_by_subdomain("nobody")) is None
    assert _run(cache.get_store_by_id("nobody")) is None
    assert _run(cache.get_store_by_domain("nobody.example.com")) is None
    assert _run(cache.get_theme("nobody")) is None


# ---------------------------------------------------------------------- #
# set_store malformed payload                                             #
# ---------------------------------------------------------------------- #


def test_set_store_missing_id_does_not_raise() -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60)
    # Bug guard: payload without 'id' should not poison the cache or raise.
    _run(cache.set_store({"subdomain": "foo", "name": "x"}))
    assert redis.store == {}


@pytest.mark.parametrize("subdomain", ["UPPER", "Mixed", "lower"])
def test_subdomain_lookup_is_case_insensitive(subdomain: str) -> None:
    redis = FakeRedis()
    cache = StorefrontCache(redis=redis, ttl_seconds=60)
    _run(cache.set_store(_payload(subdomain=subdomain)))
    # Round-trip works regardless of input case
    assert _run(cache.get_store_by_subdomain(subdomain.upper())) is not None
    assert _run(cache.get_store_by_subdomain(subdomain.lower())) is not None

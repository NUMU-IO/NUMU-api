"""Unit tests for :class:`IdempotencyKeys`.

The helper is layer 2 of the Step 09 async-tracking dedupe stack
(client UUID → Redis SET NX → DB UNIQUE). Tests cover:

* First claim on a key returns True (proceed to enqueue)
* Second claim on the same key returns False (already enqueued)
* Redis outage degrades open (returns True) so the DB UNIQUE
  constraint can still catch the eventual duplicate at the worker
* Keys live in their own ``idempotent:`` namespace

Sync ``def test_*`` + private asyncio loop pattern from Step 04 —
keeps the tests independent of the project's session-scoped asyncpg
pool which interacts badly with pytest-asyncio on Windows.
"""

from __future__ import annotations

import asyncio
from typing import Any

from redis.exceptions import RedisError

from src.infrastructure.cache.idempotency_keys import IdempotencyKeys


class FakeRedis:
    """In-memory ``redis.asyncio.Redis`` stand-in for SET NX EX."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> Any:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True


class BrokenRedis:
    async def set(self, *a: Any, **k: Any) -> Any:
        raise RedisError("simulated outage")


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_first_claim_returns_true() -> None:
    redis = FakeRedis()
    keys = IdempotencyKeys(redis=redis, ttl_seconds=60)
    assert _run(keys.claim("evt-1")) is True
    assert "idempotent:evt-1" in redis.store
    assert redis.ttls["idempotent:evt-1"] == 60


def test_second_claim_on_same_key_returns_false() -> None:
    redis = FakeRedis()
    keys = IdempotencyKeys(redis=redis, ttl_seconds=60)
    assert _run(keys.claim("evt-1")) is True
    assert _run(keys.claim("evt-1")) is False


def test_different_keys_are_independent() -> None:
    redis = FakeRedis()
    keys = IdempotencyKeys(redis=redis, ttl_seconds=60)
    assert _run(keys.claim("evt-1")) is True
    assert _run(keys.claim("evt-2")) is True


def test_redis_outage_degrades_open() -> None:
    """If Redis is unreachable, claim must return True so the caller
    still pushes the event. The DB UNIQUE constraint is the final
    safety net at the worker."""
    keys = IdempotencyKeys(redis=BrokenRedis(), ttl_seconds=60)
    # No exception escapes; the helper logs and returns True.
    assert _run(keys.claim("evt-42")) is True


def test_namespace_isolation() -> None:
    redis = FakeRedis()
    keys = IdempotencyKeys(redis=redis, ttl_seconds=60, namespace="other")
    _run(keys.claim("evt-1"))
    assert "other:evt-1" in redis.store
    assert "idempotent:evt-1" not in redis.store

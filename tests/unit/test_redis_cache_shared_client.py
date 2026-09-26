"""RedisCacheService() is cheap: one client per event loop and URL, never closed by a caller."""

import asyncio

from src.infrastructure.cache.redis_cache import RedisCacheService


async def _clients():
    a, b = RedisCacheService("redis://x:1/0"), RedisCacheService("redis://x:1/0")
    other = RedisCacheService("redis://x:1/1")
    ca, cb, co = await a._get_client(), await b._get_client(), await other._get_client()
    await a.close()
    return (
        ca,
        cb,
        co,
        await b._get_client(),
        await RedisCacheService("redis://x:1/0")._get_client(),
    )


def test_shared_per_loop_and_url():
    ca, cb, co, cb_after_close, fresh = asyncio.run(_clients())
    assert ca is cb is cb_after_close is fresh
    assert co is not ca


def test_new_loop_gets_its_own_client():
    first = asyncio.run(RedisCacheService("redis://x:1/0")._get_client())
    second = asyncio.run(RedisCacheService("redis://x:1/0")._get_client())
    assert first is not second

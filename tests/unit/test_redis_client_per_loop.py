"""A long-lived cache object hands out the client of the running event loop.

A redis.asyncio client belongs to the loop it was made on, and Celery runs
each task on a new loop. ``get_product_cache()`` is a process-wide singleton,
so a client saved on its first use would be reused on later, closed loops
(Seer on PR #771). Same for the storefront response cache's client.
"""

import asyncio

from src.infrastructure.cache import response_cache
from src.infrastructure.cache.redis_cache import RedisCacheService


def test_one_service_gets_a_client_per_event_loop():
    service = RedisCacheService("redis://localhost:6379/0")
    first = asyncio.run(service._get_client())
    second = asyncio.run(service._get_client())
    assert first is not second


def test_the_response_cache_client_follows_the_event_loop():
    async def client():
        return response_cache.response_cache_redis()

    assert asyncio.run(client()) is not asyncio.run(client())

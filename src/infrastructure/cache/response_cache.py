"""Storage for whole storefront responses (see api/middleware/storefront_response_cache)."""

from __future__ import annotations

import asyncio
import weakref
from uuid import UUID

import redis.asyncio as redis
from redis.exceptions import RedisError

from src.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

PREFIX = "sfresp:v1"
TTL_SECONDS = 60

_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, redis.Redis] = (
    weakref.WeakKeyDictionary()
)


def response_cache_redis() -> redis.Redis:
    """One bytes client per event loop (bodies are gzip, so no decode_responses).

    A redis.asyncio client belongs to the loop it was made on, and Celery runs
    each task on a new one, so a single module-wide client fails there.
    """
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = _clients[loop] = redis.from_url(
            settings.redis_url, decode_responses=False
        )
    return client


def store_prefix(store_id: UUID | str) -> str:
    return f"{PREFIX}:{str(store_id).lower()}"


async def bust_storefront_responses(store_id: UUID | str) -> None:
    """Drop every cached storefront response of one store. Never raises."""
    try:
        client = response_cache_redis()
        keys = [k async for k in client.scan_iter(match=f"{store_prefix(store_id)}:*")]
        if keys:
            await client.delete(*keys)
    except RedisError as exc:
        logger.warning("storefront_response_cache_bust_failed", error=str(exc))

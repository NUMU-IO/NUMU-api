"""Storage for whole storefront responses (see api/middleware/storefront_response_cache)."""

from __future__ import annotations

from uuid import UUID

import redis.asyncio as redis
from redis.exceptions import RedisError

from src.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

PREFIX = "sfresp:v1"
TTL_SECONDS = 60

_client: redis.Redis | None = None


def response_cache_redis() -> redis.Redis:
    """One shared bytes client (bodies are gzip, so no decode_responses)."""
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=False)
    return _client


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

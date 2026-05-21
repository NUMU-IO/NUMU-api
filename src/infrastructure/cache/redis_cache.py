"""Redis cache service implementation."""

import json
import logging
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from src.config import settings
from src.core.interfaces.services.cache_service import ICacheService

logger = logging.getLogger(__name__)


class RedisCacheService(ICacheService):
    """Cache service implementation using Redis.

    Every public method degrades a ``RedisError`` (connection lost,
    timeout, auth failure, …) to a cache-miss sentinel:

    - ``get`` returns ``None``
    - ``set`` / ``set_if_absent`` return ``False``
    - ``delete`` / ``exists`` return ``False``
    - ``clear_pattern`` returns ``0``
    - ``increment`` returns ``0``
    - ``get_many`` returns ``{}``
    - ``set_many`` returns ``False``

    Why: cached endpoints (storefront `/products`, dashboard, …) call
    the cache before falling back to the DB. Before this guard, a Redis
    outage propagated as a 500 from every cached endpoint. With it, the
    endpoints transparently fall through to the DB path. The warning is
    logged once per failed call so operators see the outage without the
    user-facing flow breaking.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or settings.redis_url
        self._client: redis.Redis | None = None

    async def _get_client(self) -> redis.Redis:
        """Get or create Redis client."""
        if self._client is None:
            self._client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._client

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.close()
            self._client = None

    def _serialize(self, value: Any) -> str:
        """Serialize value to JSON string."""
        return json.dumps(value)

    def _deserialize(self, value: str | None) -> Any:
        """Deserialize JSON string to value."""
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    async def get(self, key: str) -> Any | None:
        """Get value from cache. Returns ``None`` on cache miss or Redis error."""
        try:
            client = await self._get_client()
            value = await client.get(key)
        except RedisError as exc:
            logger.warning("redis_cache.get failed (key=%s): %s", key, exc)
            return None
        return self._deserialize(value)

    async def set(
        self,
        key: str,
        value: Any,
        expire: int | None = None,
    ) -> bool:
        """Set value in cache with optional expiration in seconds."""
        try:
            client = await self._get_client()
            serialized = self._serialize(value)
            if expire:
                return await client.setex(key, expire, serialized)
            return await client.set(key, serialized)
        except RedisError as exc:
            logger.warning("redis_cache.set failed (key=%s): %s", key, exc)
            return False

    async def set_if_absent(
        self,
        key: str,
        value: Any,
        expire: int | None = None,
    ) -> bool:
        """Set key only if it does not already exist (atomic SETNX).

        Args:
            key: Cache key.
            value: Value to store.
            expire: Optional TTL in seconds.

        Returns:
            True if the key was newly set, False if it already existed
            or if Redis is unavailable. Callers that depend on this for
            mutual exclusion must therefore treat ``False`` as "did NOT
            acquire the lock" — same behaviour as a race lose.
        """
        try:
            client = await self._get_client()
            serialized = self._serialize(value)
            result = await client.set(key, serialized, ex=expire, nx=True)
        except RedisError as exc:
            logger.warning("redis_cache.set_if_absent failed (key=%s): %s", key, exc)
            return False
        return result is not None and result is not False

    async def delete(self, key: str) -> bool:
        """Delete value from cache."""
        try:
            client = await self._get_client()
            result = await client.delete(key)
        except RedisError as exc:
            logger.warning("redis_cache.delete failed (key=%s): %s", key, exc)
            return False
        return result > 0

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        try:
            client = await self._get_client()
            return await client.exists(key) > 0
        except RedisError as exc:
            logger.warning("redis_cache.exists failed (key=%s): %s", key, exc)
            return False

    async def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching pattern."""
        try:
            client = await self._get_client()
            keys = []
            async for key in client.scan_iter(match=pattern):
                keys.append(key)
            if keys:
                return await client.delete(*keys)
            return 0
        except RedisError as exc:
            logger.warning(
                "redis_cache.clear_pattern failed (pattern=%s): %s",
                pattern,
                exc,
            )
            return 0

    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        try:
            client = await self._get_client()
            return await client.incrby(key, amount)
        except RedisError as exc:
            logger.warning("redis_cache.increment failed (key=%s): %s", key, exc)
            return 0

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values from cache."""
        try:
            client = await self._get_client()
            values = await client.mget(keys)
        except RedisError as exc:
            logger.warning("redis_cache.get_many failed (%d keys): %s", len(keys), exc)
            return {}
        return {
            key: self._deserialize(value)
            for key, value in zip(keys, values)
            if value is not None
        }

    async def set_many(
        self,
        mapping: dict[str, Any],
        expire: int | None = None,
    ) -> bool:
        """Set multiple values in cache."""
        try:
            client = await self._get_client()
            pipe = client.pipeline()
            for key, value in mapping.items():
                serialized = self._serialize(value)
                if expire:
                    pipe.setex(key, expire, serialized)
                else:
                    pipe.set(key, serialized)
            await pipe.execute()
        except RedisError as exc:
            logger.warning(
                "redis_cache.set_many failed (%d keys): %s", len(mapping), exc
            )
            return False
        return True

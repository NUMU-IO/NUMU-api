"""Redis cache service implementation."""

import json
from typing import Any

import redis.asyncio as redis

from src.config import settings
from src.core.interfaces.services.cache_service import ICacheService


class RedisCacheService(ICacheService):
    """Cache service implementation using Redis."""

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
        """Get value from cache."""
        client = await self._get_client()
        value = await client.get(key)
        return self._deserialize(value)

    async def set(
        self,
        key: str,
        value: Any,
        expire: int | None = None,
    ) -> bool:
        """Set value in cache with optional expiration in seconds."""
        client = await self._get_client()
        serialized = self._serialize(value)
        if expire:
            return await client.setex(key, expire, serialized)
        return await client.set(key, serialized)

    async def delete(self, key: str) -> bool:
        """Delete value from cache."""
        client = await self._get_client()
        result = await client.delete(key)
        return result > 0

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        client = await self._get_client()
        return await client.exists(key) > 0

    async def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching pattern."""
        client = await self._get_client()
        keys = []
        async for key in client.scan_iter(match=pattern):
            keys.append(key)
        if keys:
            return await client.delete(*keys)
        return 0

    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        client = await self._get_client()
        return await client.incrby(key, amount)

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values from cache."""
        client = await self._get_client()
        values = await client.mget(keys)
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
        client = await self._get_client()
        pipe = client.pipeline()
        for key, value in mapping.items():
            serialized = self._serialize(value)
            if expire:
                pipe.setex(key, expire, serialized)
            else:
                pipe.set(key, serialized)
        await pipe.execute()
        return True

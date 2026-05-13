"""Redis cache for the storefront store/theme read paths.

Caches the public-store payload (returned by
``GET /storefront/store-by-subdomain/{subdomain}`` and
``GET /storefront/store-by-domain/{domain}``) and theme settings,
with explicit invalidation hooks on the mutation paths and a short
TTL as the safety net.

Pattern matches :class:`PromotionCache` — wraps a raw
``redis.asyncio.Redis`` client, pipelines multi-key writes, swallows
``RedisError`` to a cache-miss sentinel so a Redis outage degrades
to direct-DB reads rather than 500s.

Kill switch: ``settings.storefront_cache_enabled = False`` makes
every ``get_*`` return ``None`` and every ``set_*`` / ``invalidate_*``
a no-op without redeploy.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from redis.exceptions import RedisError

if TYPE_CHECKING:  # pragma: no cover
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Sentinel value stored in the negative-cache slot. Returned distinct from
# ``None`` so callers can tell "cached: store does not exist" from
# "cache miss, ask the DB".
MISSING_SENTINEL = "__missing__"


def _coerce_id(value: str | UUID) -> str:
    return str(value)


def _norm(host: str) -> str:
    return host.lower().strip()


class StorefrontCache:
    """Redis-backed cache for storefront store + theme reads."""

    STORE_BY_SUBDOMAIN = "store:by_subdomain:{subdomain}"
    STORE_BY_ID = "store:by_id:{store_id}"
    STORE_BY_DOMAIN = "store:by_domain:{custom_domain}"
    THEME_BY_STORE = "theme:by_store:{store_id}"

    DEFAULT_TTL = 60
    DEFAULT_NEGATIVE_TTL = 10

    def __init__(
        self,
        redis: Redis | Any,
        *,
        ttl_seconds: int = DEFAULT_TTL,
        theme_ttl_seconds: int = DEFAULT_TTL,
        negative_ttl_seconds: int = DEFAULT_NEGATIVE_TTL,
        enabled: bool = True,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._theme_ttl = theme_ttl_seconds
        self._negative_ttl = negative_ttl_seconds
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------ #
    # Store payload                                                       #
    # ------------------------------------------------------------------ #

    async def get_store_by_subdomain(self, subdomain: str) -> dict | str | None:
        return await self._get(
            self.STORE_BY_SUBDOMAIN.format(subdomain=_norm(subdomain))
        )

    async def get_store_by_id(self, store_id: str | UUID) -> dict | str | None:
        return await self._get(self.STORE_BY_ID.format(store_id=_coerce_id(store_id)))

    async def get_store_by_domain(self, custom_domain: str) -> dict | str | None:
        return await self._get(
            self.STORE_BY_DOMAIN.format(custom_domain=_norm(custom_domain))
        )

    async def set_store(self, payload: dict) -> None:
        """Cache the public-store payload across all three lookup keys.

        ``payload`` must include at least ``id`` and ``subdomain``;
        ``custom_domain`` is optional. Writes are pipelined for atomicity.
        """
        if not self._enabled:
            return
        try:
            store_id = payload["id"]
            subdomain = payload.get("subdomain")
        except KeyError:
            logger.warning("storefront_cache.set_store: missing 'id' in payload")
            return
        serialized = json.dumps(payload)
        try:
            async with self._redis.pipeline() as pipe:
                pipe.setex(
                    self.STORE_BY_ID.format(store_id=_coerce_id(store_id)),
                    self._ttl,
                    serialized,
                )
                if subdomain:
                    pipe.setex(
                        self.STORE_BY_SUBDOMAIN.format(subdomain=_norm(subdomain)),
                        self._ttl,
                        serialized,
                    )
                custom_domain = payload.get("custom_domain")
                if custom_domain:
                    pipe.setex(
                        self.STORE_BY_DOMAIN.format(custom_domain=_norm(custom_domain)),
                        self._ttl,
                        serialized,
                    )
                await pipe.execute()
        except RedisError as exc:
            logger.warning("storefront_cache.set_store failed: %s", exc)

    async def set_store_missing(
        self, *, subdomain: str | None = None, custom_domain: str | None = None
    ) -> None:
        """Mark a subdomain or domain as known-missing for ``negative_ttl_seconds``.

        Absorbs bot probing for non-existent storefronts without hammering the DB.
        """
        if not self._enabled:
            return
        if not subdomain and not custom_domain:
            return
        try:
            async with self._redis.pipeline() as pipe:
                if subdomain:
                    pipe.setex(
                        self.STORE_BY_SUBDOMAIN.format(subdomain=_norm(subdomain)),
                        self._negative_ttl,
                        MISSING_SENTINEL,
                    )
                if custom_domain:
                    pipe.setex(
                        self.STORE_BY_DOMAIN.format(custom_domain=_norm(custom_domain)),
                        self._negative_ttl,
                        MISSING_SENTINEL,
                    )
                await pipe.execute()
        except RedisError as exc:
            logger.warning("storefront_cache.set_store_missing failed: %s", exc)

    async def invalidate_store(
        self,
        *,
        store_id: str | UUID,
        subdomain: str | None = None,
        custom_domain: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        keys: list[str] = [self.STORE_BY_ID.format(store_id=_coerce_id(store_id))]
        if subdomain:
            keys.append(self.STORE_BY_SUBDOMAIN.format(subdomain=_norm(subdomain)))
        if custom_domain:
            keys.append(self.STORE_BY_DOMAIN.format(custom_domain=_norm(custom_domain)))
        try:
            await self._redis.delete(*keys)
        except RedisError as exc:
            logger.warning("storefront_cache.invalidate_store failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Theme payload                                                       #
    # ------------------------------------------------------------------ #

    async def get_theme(self, store_id: str | UUID) -> dict | None:
        value = await self._get(
            self.THEME_BY_STORE.format(store_id=_coerce_id(store_id))
        )
        if value == MISSING_SENTINEL:
            return None
        return value if isinstance(value, dict) else None

    async def set_theme(self, store_id: str | UUID, payload: dict) -> None:
        if not self._enabled:
            return
        try:
            await self._redis.setex(
                self.THEME_BY_STORE.format(store_id=_coerce_id(store_id)),
                self._theme_ttl,
                json.dumps(payload),
            )
        except RedisError as exc:
            logger.warning("storefront_cache.set_theme failed: %s", exc)

    async def invalidate_theme(self, store_id: str | UUID) -> None:
        if not self._enabled:
            return
        try:
            await self._redis.delete(
                self.THEME_BY_STORE.format(store_id=_coerce_id(store_id))
            )
        except RedisError as exc:
            logger.warning("storefront_cache.invalidate_theme failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    async def _get(self, key: str) -> dict | str | None:
        if not self._enabled:
            return None
        try:
            raw = await self._redis.get(key)
        except RedisError as exc:
            logger.warning("storefront_cache.get failed (key=%s): %s", key, exc)
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        if raw == MISSING_SENTINEL:
            return MISSING_SENTINEL
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


# ----------------------------------------------------------------------
# Singleton + factory
# ----------------------------------------------------------------------

_storefront_cache_instance: StorefrontCache | None = None


def get_storefront_cache() -> StorefrontCache:
    """Get or create the process-wide StorefrontCache singleton.

    Builds its Redis client from ``settings.redis_url`` on first call.
    Returns a kill-switched cache (no-op reads/writes) when
    ``settings.storefront_cache_enabled`` is ``False``.
    """
    global _storefront_cache_instance
    if _storefront_cache_instance is None:
        import redis.asyncio as redis_async

        from src.config import settings

        client = redis_async.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        _storefront_cache_instance = StorefrontCache(
            redis=client,
            ttl_seconds=settings.cache_ttl_store_seconds,
            theme_ttl_seconds=settings.cache_ttl_theme_seconds,
            negative_ttl_seconds=settings.cache_negative_ttl_seconds,
            enabled=settings.storefront_cache_enabled,
        )
    return _storefront_cache_instance


def reset_storefront_cache_singleton() -> None:
    """Test hook — clears the cached singleton so the next call rebuilds.

    Used by integration tests that need to bind to a different Redis URL
    or flip the kill switch.
    """
    global _storefront_cache_instance
    _storefront_cache_instance = None

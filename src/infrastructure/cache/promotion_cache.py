"""Redis cache wrapper for the storefront `active promotions` resolver.

Per-store SET stores all live cache keys so we can invalidate without
relying on `KEYS` / `SCAN` patterns. TTL on the SET is generous to
survive normal churn but auto-expires so abandoned merchants don't leak
keys forever.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:  # pragma: no cover - avoid heavy import at module load
    from redis.asyncio import Redis

from src.application.dto.promotion_resolution import (
    ActivePromotionsOutput,
    VisitorContextInput,
)


def visitor_fingerprint(visitor: VisitorContextInput) -> str:
    """Coarse hash of the eligibility-relevant visitor fields.

    Coarse enough that hot caches actually hit, fine enough that two
    visitors with materially different targeting see different cached
    payloads. Cart contents intentionally NOT in the fingerprint — the
    cart-side discount calc is its own use case.
    """
    parts: list[str] = [
        visitor.locale,
        visitor.device,
        visitor.country or "",
        visitor.city or "",
        "1" if visitor.is_logged_in else "0",
        "1" if visitor.is_first_visit else "0",
        ",".join(sorted(visitor.customer_tags)),
        visitor.page_path or "/",
    ]
    raw = "|".join(parts).encode()
    # Stable cache key, NOT a security primitive. The fingerprint just
    # buckets cache slots — collisions only cost us a recompute.
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:16]  # noqa: S324


class PromotionCache:
    """Redis-backed cache for `ResolveActivePromotionsUseCase` outputs."""

    KEY_TEMPLATE = "promotions:active:{store_id}:{visitor_fp}"
    LIST_KEY = "promotions:active_keys:{store_id}"
    DEFAULT_TTL = 60  # seconds — matches `cache_ttl_seconds` in the DTO

    def __init__(self, redis: Redis | Any, ttl_seconds: int = DEFAULT_TTL) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    # ------------------------------------------------------------------ #
    # Read / write                                                        #
    # ------------------------------------------------------------------ #

    async def get(
        self, store_id: UUID, visitor: VisitorContextInput
    ) -> ActivePromotionsOutput | None:
        fp = visitor_fingerprint(visitor)
        key = self.KEY_TEMPLATE.format(store_id=store_id, visitor_fp=fp)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return ActivePromotionsOutput.model_validate_json(raw)

    async def set(
        self,
        store_id: UUID,
        visitor: VisitorContextInput,
        value: ActivePromotionsOutput,
    ) -> None:
        fp = visitor_fingerprint(visitor)
        key = self.KEY_TEMPLATE.format(store_id=store_id, visitor_fp=fp)
        list_key = self.LIST_KEY.format(store_id=store_id)
        async with self._redis.pipeline() as pipe:
            pipe.setex(key, self._ttl, value.model_dump_json())
            pipe.sadd(list_key, key)
            pipe.expire(list_key, self._ttl * 5)
            await pipe.execute()

    async def invalidate_store(self, store_id: UUID) -> None:
        """Drop every cached entry for a single store."""
        list_key = self.LIST_KEY.format(store_id=store_id)
        members = await self._redis.smembers(list_key)
        if members:
            await self._redis.delete(*members, list_key)

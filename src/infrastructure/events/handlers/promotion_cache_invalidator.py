"""Cache invalidation subscriber for promotion lifecycle events.

Wired into the EventBus by `infrastructure/events/setup.py`. Whenever a
promotion is created / updated / deleted, the store's cache slot is
flushed so subsequent storefront reads recompute.
"""

from src.config.logging_config import get_logger
from src.core.events.promotion_events import (
    PromotionCreatedEvent,
    PromotionDeletedEvent,
    PromotionUpdatedEvent,
)
from src.infrastructure.cache.promotion_cache import PromotionCache

logger = get_logger(__name__)


class PromotionCacheInvalidator:
    """Holds a `PromotionCache` instance and exposes one handler per event.

    Stored as a single object so `setup.py` can wire all three handlers
    against the same cache backend without leaking the Redis dep.
    """

    def __init__(self, cache: PromotionCache) -> None:
        self._cache = cache

    async def on_created(self, event: PromotionCreatedEvent) -> None:
        await self._cache.invalidate_store(event.store_id)
        logger.info(
            "promotion_cache_invalidated",
            reason="created",
            store_id=str(event.store_id),
            promotion_id=str(event.promotion_id),
        )

    async def on_updated(self, event: PromotionUpdatedEvent) -> None:
        await self._cache.invalidate_store(event.store_id)
        logger.info(
            "promotion_cache_invalidated",
            reason="updated",
            store_id=str(event.store_id),
            promotion_id=str(event.promotion_id),
            new_version=event.new_version,
        )

    async def on_deleted(self, event: PromotionDeletedEvent) -> None:
        await self._cache.invalidate_store(event.store_id)
        logger.info(
            "promotion_cache_invalidated",
            reason="deleted",
            store_id=str(event.store_id),
            promotion_id=str(event.promotion_id),
        )

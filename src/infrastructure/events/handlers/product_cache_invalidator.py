"""Cache invalidation subscriber for product lifecycle events.

Wired into the EventBus by ``infrastructure/events/setup.py``. Whenever a
product is created / updated / deleted, the store's product cache slots
(listing pages + the product detail entry) are flushed so subsequent
storefront reads recompute from the DB.

Dispatch is deferred until the request transaction commits (see
``deferred_dispatch``), so invalidation always runs *after* the write is
durable — a racing storefront read cannot re-cache the pre-commit row.
This is the same post-commit guarantee the promotion cache invalidator
relies on.
"""

from uuid import UUID

from src.core.events.commerce_events import InventoryLevelChangedEvent
from src.core.events.product_events import (
    ProductCreatedEvent,
    ProductDeletedEvent,
    ProductUpdatedEvent,
)
from src.core.logging import get_logger
from src.infrastructure.cache.product_cache import ProductCacheService

logger = get_logger(__name__)


class ProductCacheInvalidator:
    """Holds a ``ProductCacheService`` and exposes one handler per event.

    Stored as a single object so ``setup.py`` can wire all three handlers
    against the same cache backend without leaking the Redis dep.
    """

    def __init__(self, cache: ProductCacheService) -> None:
        self._cache = cache
        self._flushing: set[UUID] = set()
        self._dirty: set[UUID] = set()

    async def on_created(self, event: ProductCreatedEvent) -> None:
        # A newly-created product only changes listing pages (no detail
        # entry can exist for it yet), so a store-wide listing sweep suffices.
        await self._cache.invalidate_store_products(event.store_id)
        logger.debug(
            "product_cache_invalidated",
            reason="created",
            store_id=str(event.store_id),
            product_id=str(event.product_id),
        )

    async def on_updated(self, event: ProductUpdatedEvent) -> None:
        # invalidate_product clears the detail slot AND every listing page
        # for the store (via the products:store:{id}:* pattern sweep), which
        # also drops any slug-keyed detail entry.
        await self._cache.invalidate_product(event.store_id, event.product_id)
        logger.debug(
            "product_cache_invalidated",
            reason="updated",
            store_id=str(event.store_id),
            product_id=str(event.product_id),
        )

    async def on_stock_changed(self, event: InventoryLevelChangedEvent) -> None:
        # One commit can change many variants (an order's lines, a stock
        # import, a variant matrix), one event each. While a store's flush
        # runs, later changes only ask for one more flush after it, so a burst
        # costs one or two store-wide flushes, not two Redis scans per variant.
        # The store-wide sweep also clears the detail entries
        # (products:store:{id}:detail:*).
        store = event.store_id
        if store in self._flushing:
            self._dirty.add(store)
            return
        self._flushing.add(store)
        try:
            while True:
                self._dirty.discard(store)
                await self._cache.invalidate_store_products(store)
                if store not in self._dirty:
                    break
        finally:
            self._flushing.discard(store)
        logger.debug("product_cache_invalidated", reason="stock", store_id=str(store))

    async def on_deleted(self, event: ProductDeletedEvent) -> None:
        await self._cache.invalidate_product(event.store_id, event.product_id)
        logger.debug(
            "product_cache_invalidated",
            reason="deleted",
            store_id=str(event.store_id),
            product_id=str(event.product_id),
        )

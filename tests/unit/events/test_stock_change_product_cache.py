"""A stock change refreshes the storefront's cached product.

The product cache (detail 30 min, lists 5 min) was flushed only on product
create/update/delete. A variant edit, an inventory adjustment or an order
changes stock without those events, so a sold-out size stayed "in stock" on
the storefront for up to 30 minutes (found on production 2026-10-14 while
testing Back in Stock: its form cannot appear while the page says in stock).
"""

from uuid import uuid4

import pytest

from src.core.events.commerce_events import InventoryLevelChangedEvent
from src.infrastructure.events.handlers.product_cache_invalidator import (
    ProductCacheInvalidator,
)


@pytest.mark.asyncio
async def test_a_stock_change_flushes_the_cached_product(monkeypatch):
    flushed = []

    class FakeCache:
        async def invalidate_product(self, store_id, product_id):
            flushed.append((store_id, product_id))

        async def invalidate_store_products(self, store_id):
            flushed.append((store_id, None))

    from src.infrastructure.events.setup import create_event_bus

    # create_event_bus() is idempotent, so another test may have built the bus
    # already: swap the cache on the subscribed invalidator itself.
    bus = create_event_bus()
    event = InventoryLevelChangedEvent(
        store_id=uuid4(), product_id=uuid4(), variant_id=uuid4()
    )
    handlers = [
        h
        for h in bus._handlers[event.event_type]
        if isinstance(getattr(h, "__self__", None), ProductCacheInvalidator)
    ]
    assert handlers, "no product-cache handler for InventoryLevelChangedEvent"
    for handler in handlers:
        monkeypatch.setattr(handler.__self__, "_cache", FakeCache())
        await handler(event)
    assert flushed == [(event.store_id, event.product_id)]

"""A stock change refreshes the storefront's cached product.

The product cache (detail 30 min, lists 5 min) was flushed only on product
create/update/delete. A variant edit, an inventory adjustment or an order
changes stock without those events, so a sold-out size stayed "in stock" on
the storefront for up to 30 minutes (found on production 2026-10-14 while
testing Back in Stock: its form cannot appear while the page says in stock).

One commit can change many variants (an order's lines, a stock import, a
variant matrix), one event each; a burst must not become two Redis scans per
variant (Seer on PR #771).
"""

import asyncio
from uuid import uuid4

import pytest

from src.core.events.commerce_events import InventoryLevelChangedEvent
from src.infrastructure.events.handlers.product_cache_invalidator import (
    ProductCacheInvalidator,
)


class FakeCache:
    def __init__(self):
        self.flushed = []

    async def invalidate_product(self, store_id, product_id):
        self.flushed.append((store_id, product_id))

    async def invalidate_store_products(self, store_id):
        await asyncio.sleep(0.01)  # a Redis scan takes a moment
        self.flushed.append((store_id, None))


def _stock_handlers():
    from src.infrastructure.events.setup import create_event_bus

    # create_event_bus() is idempotent, so another test may have built the bus
    # already: the cache is swapped on the subscribed invalidator itself.
    bus = create_event_bus()
    event_type = InventoryLevelChangedEvent(
        store_id=uuid4(), product_id=uuid4()
    ).event_type
    return [
        h
        for h in bus._handlers[event_type]
        if isinstance(getattr(h, "__self__", None), ProductCacheInvalidator)
    ]


@pytest.mark.asyncio
async def test_a_stock_change_flushes_the_stores_cached_products(monkeypatch):
    handlers = _stock_handlers()
    assert handlers, "no product-cache handler for InventoryLevelChangedEvent"
    cache = FakeCache()
    event = InventoryLevelChangedEvent(
        store_id=uuid4(), product_id=uuid4(), variant_id=uuid4()
    )
    for handler in handlers:
        monkeypatch.setattr(handler.__self__, "_cache", cache)
        await handler(event)
    assert cache.flushed == [(event.store_id, None)]


@pytest.mark.asyncio
async def test_a_burst_of_stock_changes_is_flushed_once_or_twice(monkeypatch):
    (handler,) = _stock_handlers()
    cache = FakeCache()
    monkeypatch.setattr(handler.__self__, "_cache", cache)
    store = uuid4()
    events = [
        InventoryLevelChangedEvent(
            store_id=store, product_id=uuid4(), variant_id=uuid4()
        )
        for _ in range(50)
    ]
    await asyncio.gather(*(handler(e) for e in events))
    # The first flush runs; every change during it asks for one more.
    assert cache.flushed in ([(store, None)], [(store, None), (store, None)])

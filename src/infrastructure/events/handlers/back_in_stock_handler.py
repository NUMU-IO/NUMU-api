"""Back in Stock app: reacts to NUMU's own events, never webhooks.

- Stock changed → a restock check in 60 seconds (BIS-D7), only if someone
  waits for that product.
- Product deleted → its waiting rows close.
- Order created → the attribution task loads the order (the event carries no
  lines and no contact).

Each handler checks the install gate on its first line, so a store without the
app costs one indexed query and nothing else.
"""

from __future__ import annotations

from src.application.services import back_in_stock as bis
from src.core.events.commerce_events import InventoryLevelChangedEvent
from src.core.events.order_events import OrderCreatedEvent
from src.core.events.product_events import ProductDeletedEvent
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories import back_in_stock_repository as repo
from src.infrastructure.tenancy.rls import enable_rls_bypass

logger = get_logger(__name__)


async def on_inventory_changed(event: InventoryLevelChangedEvent) -> None:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if not await repo.is_live(db, event.store_id):
            return
        if not await repo.has_waiting(db, event.store_id, event.product_id):
            return
    from src.infrastructure.messaging.tasks.back_in_stock_app_tasks import (
        restock_check_task,
    )

    restock_check_task.apply_async(
        args=[str(event.store_id), str(event.product_id)],
        countdown=bis.CHECK_DELAY_SECONDS,
    )


async def on_product_deleted(event: ProductDeletedEvent) -> None:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if not await repo.is_live(db, event.store_id):
            return
        closed = await repo.close_product(db, event.store_id, event.product_id)
        await db.commit()
    if closed:
        logger.info("back_in_stock_closed_for_deleted_product", closed=closed)


async def on_order_created(event: OrderCreatedEvent) -> None:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if not await repo.is_live(db, event.store_id):
            return
    from src.infrastructure.messaging.tasks.back_in_stock_app_tasks import (
        attribution_task,
    )

    attribution_task.apply_async(args=[str(event.order_id)])

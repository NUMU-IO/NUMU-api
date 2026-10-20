"""Reviews app: an order's status decides its review request.

- ``delivered`` → the order's one request, due a few days later inside the
  store's send window. The sweep sends it; no Celery countdown spans days.
- ``returned``, ``refunded``, ``cancelled`` → a waiting request is called off.

The install gate is the first query, so a store without the app costs one
indexed lookup. The send re-checks everything when the request comes due.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.application.services import reviews_service as svc
from src.application.services.app_install_gate import is_live
from src.core.events.order_events import OrderStatusChangedEvent
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories import reviews_repository as repo
from src.infrastructure.tenancy.rls import enable_rls_bypass


async def on_order_status_changed(event: OrderStatusChangedEvent) -> None:
    status = str(event.new_status)
    if status != "delivered" and status not in svc.CALL_OFF:
        return
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if not await is_live(db, event.store_id, svc.SLUG):
            return
        now = datetime.now(UTC)
        if status == "delivered":
            store = await db.get(StoreModel, event.store_id)
            await repo.schedule(db, store, event.order_id, now)
        else:
            await repo.call_off(db, event.order_id, now)
        await db.commit()

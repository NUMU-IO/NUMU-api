"""Order Tracking app: reacts to NUMU's own events, never webhooks.

- A forward shipment created, or an order marked shipped → its parcel row
  (area and promised window, fixed now).
- A forward shipment moved into ``out_for_delivery`` or ``failed`` → one
  WhatsApp message for that attempt, at once or at 09:00 after quiet hours.

The events carry no ``tenant_id`` and no attempt count, so both come from the
rows. The install gate is each handler's first query, so a store without the
app costs one indexed lookup. Bulk SQL updates and carrier polls whose
handlers never ran are found again by the tasks' sweeps.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.application.services import order_tracking_service as svc
from src.application.services.app_install_gate import is_live
from src.core.events.commerce_events import (
    ShipmentCreatedEvent,
    ShipmentStatusChangedEvent,
)
from src.core.events.order_events import OrderStatusChangedEvent
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories import order_tracking_repository as repo
from src.infrastructure.tenancy.rls import enable_rls_bypass


async def on_shipment_created(event: ShipmentCreatedEvent) -> None:
    if event.shipment_type != "forward":
        return
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if not await is_live(db, event.store_id, svc.SLUG):
            return
        await repo.track(db, event.order_id)
        await db.commit()


async def on_order_status_changed(event: OrderStatusChangedEvent) -> None:
    """An order marked shipped by hand may never get a shipment."""
    if event.new_status != "shipped":
        return
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if not await is_live(db, event.store_id, svc.SLUG):
            return
        await repo.track(db, event.order_id)
        await db.commit()


async def on_shipment_status_changed(event: ShipmentStatusChangedEvent) -> None:
    # The event also fires when only the tracking number changed.
    if event.status == event.previous_status or event.shipment_type != "forward":
        return
    kind = svc.KIND_OF_STATUS.get(event.status)
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if not await is_live(db, event.store_id, svc.SLUG):
            return
        await repo.track(db, event.order_id)
        message = None
        shipment = await repo.shipment(db, event.shipment_id) if kind else None
        if shipment is not None and shipment.status == event.status:
            store = await db.get(StoreModel, event.store_id)
            message = await repo.schedule(
                db,
                store,
                event.order_id,
                kind,
                svc.entries(shipment.status_history, event.status),
                datetime.now(UTC),
            )
        await db.commit()
    if message is not None and message.status == svc.SCHEDULED:
        from src.infrastructure.messaging.tasks.order_tracking_tasks import send_task

        send_task.apply_async(args=[str(message.id)], eta=message.send_at)

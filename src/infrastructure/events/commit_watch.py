"""Customer, refund, shipment and stock events, from every ORM write.

Those rows are written from many places: checkout, the hub, carrier
webhooks, imports and Celery tasks. Watching the flush is the one place that
sees them all. Events are held on the session that flushed the change and
published only when that same session commits; a rollback drops them, so a
change that did not persist never reaches a webhook. Several flushes of one
row in one transaction publish one event.

Bulk ``update()`` statements bypass the ORM and are not seen.
"""

import asyncio

from sqlalchemy import event as sa_event
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from src.core.events.base import DomainEvent, EventBus, _schedule_immediately
from src.core.events.commerce_events import (
    CustomerCreatedEvent,
    CustomerUpdatedEvent,
    InventoryLevelChangedEvent,
    RefundCompletedEvent,
    RefundCreatedEvent,
    ShipmentCreatedEvent,
    ShipmentStatusChangedEvent,
)
from src.core.logging import get_logger
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.refund import RefundModel
from src.infrastructure.database.models.tenant.shipment import ShipmentModel
from src.infrastructure.database.models.tenant.variant import VariantModel

logger = get_logger(__name__)

_KEY = "_commit_watch_events"
_bus: EventBus | None = None


def _value(v):
    return getattr(v, "value", v)


def _changed(obj, attr: str) -> bool:
    return inspect(obj).attrs[attr].history.has_changes()


def _previous(obj, attr: str):
    deleted = inspect(obj).attrs[attr].history.deleted
    return _value(deleted[0] if deleted else getattr(obj, attr))


def _customer(cls, m: CustomerModel) -> DomainEvent:
    return cls(
        store_id=m.store_id,
        customer_id=m.id,
        email=m.email,
        phone=m.phone,
        first_name=m.first_name,
        last_name=m.last_name,
        accepts_marketing=bool(m.accepts_marketing),
    )


def _refund(cls, m: RefundModel) -> DomainEvent:
    return cls(
        store_id=m.store_id,
        refund_id=m.id,
        refund_number=m.refund_number,
        order_id=m.order_id,
        status=_value(m.status),
        amount_cents=m.amount or 0,
        currency=m.currency or "EGP",
    )


def _shipment(cls, m: ShipmentModel, **extra) -> DomainEvent:
    return cls(
        store_id=m.store_id,
        shipment_id=m.id,
        order_id=m.order_id,
        shipment_type=m.shipment_type or "forward",
        carrier=m.carrier,
        status=_value(m.status),
        tracking_number=m.tracking_number,
        tracking_url=m.tracking_url,
        **extra,
    )


def _events_for_new(obj) -> list[DomainEvent]:
    if isinstance(obj, CustomerModel):
        return [_customer(CustomerCreatedEvent, obj)]
    if isinstance(obj, RefundModel):
        out = [_refund(RefundCreatedEvent, obj)]
        if _value(obj.status) == "completed":
            out.append(_refund(RefundCompletedEvent, obj))
        return out
    if isinstance(obj, ShipmentModel):
        return [_shipment(ShipmentCreatedEvent, obj)]
    return []


def _events_for_dirty(obj) -> list[DomainEvent]:
    if isinstance(obj, CustomerModel):
        return [_customer(CustomerUpdatedEvent, obj)]
    if isinstance(obj, RefundModel):
        if (
            _changed(obj, "status")
            and _value(obj.status) == "completed"
            and _previous(obj, "status") != "completed"
        ):
            return [_refund(RefundCompletedEvent, obj)]
    elif isinstance(obj, ShipmentModel):
        if _changed(obj, "status") or _changed(obj, "tracking_number"):
            return [
                _shipment(
                    ShipmentStatusChangedEvent,
                    obj,
                    previous_status=_previous(obj, "status"),
                )
            ]
    elif isinstance(obj, ProductModel):
        if _changed(obj, "quantity"):
            return [
                InventoryLevelChangedEvent(store_id=obj.store_id, product_id=obj.id)
            ]
    elif isinstance(obj, VariantModel):
        if _changed(obj, "inventory_quantity"):
            return [
                InventoryLevelChangedEvent(
                    store_id=obj.store_id, product_id=obj.product_id, variant_id=obj.id
                )
            ]
    return []


_IDS = {"customer_id", "refund_id", "shipment_id", "product_id", "variant_id"}


def _key(e: DomainEvent) -> tuple:
    ids = sorted(e.model_dump(include=_IDS).items())
    return (e.event_type, *(str(v) for _, v in ids))


def _created_key(e: DomainEvent) -> tuple | None:
    created = {
        "CustomerUpdatedEvent": "CustomerCreatedEvent",
        "ShipmentStatusChangedEvent": "ShipmentCreatedEvent",
    }.get(e.event_type)
    if created is None:
        return None
    return (created, *_key(e)[1:])


def _collect(session: Session, _flush_context) -> None:
    try:
        pending: dict = session.info.setdefault(_KEY, {})
        found = [e for obj in session.new for e in _events_for_new(obj)]
        found += [
            e
            for obj in session.dirty
            if session.is_modified(obj, include_collections=False)
            for e in _events_for_dirty(obj)
        ]
        for e in found:
            if _created_key(e) in pending:
                continue
            pending[_key(e)] = e
    except Exception:
        logger.exception("commit_watch_collect_failed")


def _publish(session: Session) -> None:
    events = session.info.pop(_KEY, None)
    if not events or _bus is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    for e in events.values():
        handlers = list(_bus._handlers.get(e.event_type, []))
        if handlers:
            _schedule_immediately(_bus, e, handlers)


def _discard(session: Session) -> None:
    session.info.pop(_KEY, None)


def install(bus: EventBus) -> None:
    global _bus
    if _bus is None:
        sa_event.listen(Session, "after_flush", _collect)
        sa_event.listen(Session, "after_commit", _publish)
        sa_event.listen(Session, "after_rollback", _discard)
    _bus = bus

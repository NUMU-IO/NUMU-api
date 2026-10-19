"""Order Tracking app: the queries its routes, handler and tasks share."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from src.application.services import order_tracking_service as svc
from src.application.services.app_install_gate import is_live
from src.core.entities.order import OrderStatus
from src.core.utils.store_timezone import (
    local_date,
    local_day_bounds,
    resolve_store_timezone_name,
)
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingMessageModel as Message,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingParcelModel as ParcelRow,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingRetryRequestModel as Retry,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingSettingsModel as Settings,
)
from src.infrastructure.database.models.tenant.shipment import ShipmentModel
from src.infrastructure.database.models.tenant.shipping_zone import (
    ShippingZoneModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel

FORWARD = ShipmentModel.shipment_type == "forward"


def _insert(db: AsyncSession, model: Any) -> Any:
    """An INSERT that can say ON CONFLICT DO NOTHING on Postgres and on the
    SQLite test database alike."""
    dialect = postgresql if db.get_bind().dialect.name == "postgresql" else sqlite
    return dialect.insert(model)


async def lock(db: AsyncSession, key: str) -> None:
    """Serialise one key until the transaction ends (Postgres only; the
    SQLite tests run one session)."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key}
        )


# ─── Settings ──────────────────────────────────────────────────────────


async def settings_row(db: AsyncSession, store_id: UUID) -> Settings | None:
    return await db.get(Settings, store_id)


async def settings(db: AsyncSession, store_id: UUID) -> dict[str, Any]:
    return svc.merged_settings(await settings_row(db, store_id))


async def ensure_settings(db: AsyncSession, store: Any) -> tuple[Settings, bool]:
    """The store's row, created with the defaults on the front's first call.
    ``True`` when this call created it (the caller queues the reconcile)."""
    row = await settings_row(db, store.id)
    if row is not None:
        return row, False
    await db.execute(
        _insert(db, Settings)
        .values(store_id=store.id, tenant_id=store.tenant_id, **svc.DEFAULT_SETTINGS)
        .on_conflict_do_nothing(index_elements=["store_id"])
    )
    return await settings_row(db, store.id), True


# ─── Orders, shipments, parcels ────────────────────────────────────────


async def order(db: AsyncSession, order_id: UUID) -> OrderModel | None:
    return await db.scalar(
        select(OrderModel).options(raiseload("*")).where(OrderModel.id == order_id)
    )


async def latest_shipment(db: AsyncSession, order_id: UUID) -> ShipmentModel | None:
    """The order's newest forward shipment: the parcel the shopper waits for."""
    return await db.scalar(
        select(ShipmentModel)
        .options(raiseload("*"))
        .where(ShipmentModel.order_id == order_id, FORWARD)
        .order_by(ShipmentModel.created_at.desc())
        .limit(1)
    )


async def shipment(db: AsyncSession, shipment_id: UUID) -> ShipmentModel | None:
    return await db.scalar(
        select(ShipmentModel)
        .options(raiseload("*"))
        .where(ShipmentModel.id == shipment_id)
    )


async def zone_days(db: AsyncSession, zone_id: UUID | None) -> tuple[int, int] | None:
    if zone_id is None:
        return None
    row = (
        await db.execute(
            select(
                ShippingZoneModel.estimated_days_min,
                ShippingZoneModel.estimated_days_max,
            ).where(ShippingZoneModel.id == zone_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


async def window_for(
    db: AsyncSession, o: OrderModel, tz_name: str
) -> tuple[Any, Any] | tuple[None, None]:
    if o.created_at is None:
        return None, None
    return svc.eta_window(
        o.created_at, tz_name, await zone_days(db, o.shipping_zone_id)
    )


async def parcel_row(
    db: AsyncSession, store_id: UUID, order_id: UUID
) -> ParcelRow | None:
    return await db.get(ParcelRow, (store_id, order_id))


async def track(db: AsyncSession, order_id: UUID) -> ParcelRow | None:
    """The order's parcel row, made once (idempotent) and pointed at its
    newest forward shipment. Area and window are fixed here, when it ships."""
    o = await order(db, order_id)
    if o is None:
        return None
    newest = await latest_shipment(db, order_id)
    row = await parcel_row(db, o.store_id, o.id)
    if row is None:
        store = await db.get(StoreModel, o.store_id)
        tz = resolve_store_timezone_name(store.settings if store else None)
        code, city = svc.area_of(o.shipping_address)
        eta_min, eta_max = await window_for(db, o, tz)
        await db.execute(
            _insert(db, ParcelRow)
            .values(
                store_id=o.store_id,
                order_id=o.id,
                tenant_id=o.tenant_id,
                shipment_id=newest.id if newest else None,
                governorate_code=code,
                city=city,
                eta_min=eta_min,
                eta_max=eta_max,
            )
            .on_conflict_do_nothing(index_elements=["store_id", "order_id"])
        )
        row = await parcel_row(db, o.store_id, o.id)
    if row is not None and newest is not None and row.shipment_id != newest.id:
        row.shipment_id = newest.id
    return row


async def parcels(
    db: AsyncSession, store_id: UUID, since: datetime
) -> list[svc.Parcel]:
    """Every parcel of the orders placed since ``since`` (the reconcile's
    window), read live from core in one query. Run on the connection, not
    the ORM: plain rows, about half the time for thousands of parcels."""
    conn = await db.connection()
    rows = (
        await conn.execute(
            select(
                ParcelRow.order_id,
                ParcelRow.governorate_code,
                ParcelRow.city,
                ParcelRow.eta_min,
                ParcelRow.eta_max,
                ParcelRow.created_at,
                OrderModel.order_number,
                OrderModel.status.label("order_status"),
                OrderModel.updated_at.label("order_updated"),
                OrderModel.fulfilled_at,
                ShipmentModel.status.label("ship_status"),
                ShipmentModel.carrier,
                ShipmentModel.status_history,
                ShipmentModel.shipped_at,
                ShipmentModel.delivered_at,
                ShipmentModel.updated_at.label("ship_updated"),
            )
            .join(OrderModel, OrderModel.id == ParcelRow.order_id)
            .outerjoin(ShipmentModel, ShipmentModel.id == ParcelRow.shipment_id)
            .where(ParcelRow.store_id == store_id, OrderModel.created_at >= since)
        )
    ).all()
    out = []
    for r in rows:
        status = svc.parcel_status(r.ship_status, r.order_status)
        history = r.status_history or []
        delivered_at = svc.aware(r.delivered_at)
        if status == "delivered" and delivered_at is None:
            # ponytail: a delivery marked on the order only; its last update
            # stands in for the delivery time.
            delivered_at = svc.aware(r.order_updated)
        out.append(
            svc.Parcel(
                order_id=r.order_id,
                order_number=r.order_number,
                status=status,
                carrier=r.carrier,
                governorate_code=r.governorate_code,
                city=r.city,
                eta_min=r.eta_min,
                eta_max=r.eta_max,
                attempts=svc.entries(history, "failed"),
                failed_at=svc.entered_at(history, "failed"),
                # Picked up by the carrier, else marked shipped on the order.
                shipped_at=svc.aware(r.shipped_at) or svc.aware(r.fulfilled_at),
                delivered_at=delivered_at,
                updated_at=max(
                    t
                    for t in (
                        svc.aware(r.ship_updated),
                        svc.aware(r.order_updated),
                        svc.aware(r.created_at),
                    )
                    if t is not None
                ),
            )
        )
    return out


# ─── Retry requests ────────────────────────────────────────────────────


async def open_retries(db: AsyncSession, store_id: UUID) -> dict[Any, Retry]:
    """The newest unhandled request per order."""
    rows = (
        (
            await db.execute(
                select(Retry)
                .where(Retry.store_id == store_id, Retry.handled_at.is_(None))
                .order_by(Retry.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {r.order_id: r for r in rows}


async def newest_retry(
    db: AsyncSession, store_id: UUID, order_id: UUID
) -> Retry | None:
    return await db.scalar(
        select(Retry)
        .where(Retry.store_id == store_id, Retry.order_id == order_id)
        .order_by(Retry.created_at.desc())
        .limit(1)
    )


async def retried_since(
    db: AsyncSession, store_id: UUID, order_id: UUID, since: datetime
) -> bool:
    return (
        await db.scalar(
            select(Retry.id)
            .where(
                Retry.store_id == store_id,
                Retry.order_id == order_id,
                Retry.created_at >= since,
            )
            .limit(1)
        )
    ) is not None


# ─── Messages ──────────────────────────────────────────────────────────


async def schedule(
    db: AsyncSession, store: Any, order_id: UUID, kind: str, attempt: int, now: datetime
) -> Message | None:
    """The (order, kind, attempt) message row, made once: ``scheduled`` for
    ``send_at``, or ``skipped`` (switch off, daily cap). None when the row
    already exists, so a second event or sweep sends nothing again."""
    cfg = await settings(db, store.id)
    tz = resolve_store_timezone_name(store.settings)
    status, reason, send_at = svc.SCHEDULED, None, svc.send_time(now, tz)
    if not cfg[svc.SWITCH[kind]]:
        status, reason, send_at = svc.SKIPPED, "switch_off", None
    else:
        # ponytail: the cap counts what was scheduled during the store's day,
        # so a message scheduled at 23:00 for 09:00 counts against that day.
        await lock(db, f"order-tracking-cap:{store.id}")
        start, end = local_day_bounds(local_date(now, tz), tz)
        today = await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.store_id == store.id,
                Message.status.in_((svc.SCHEDULED, svc.SENT)),
                Message.created_at >= start,
                Message.created_at < end,
            )
        )
        if (today or 0) >= cfg["daily_cap"]:
            status, reason, send_at = svc.SKIPPED, "cap", None
    new_id = await db.scalar(
        _insert(db, Message)
        .values(
            id=uuid4(),
            store_id=store.id,
            order_id=order_id,
            tenant_id=store.tenant_id,
            kind=kind,
            attempt=attempt,
            status=status,
            skip_reason=reason,
            send_at=send_at,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["store_id", "order_id", "kind", "attempt"]
        )
        .returning(Message.id)
    )
    return await db.get(Message, new_id) if new_id else None


async def live_store_ids(db: AsyncSession) -> list[UUID]:
    """Stores the app is live on (each checked through the one gate)."""
    installed = (
        (
            await db.execute(
                select(AppInstallationModel.store_id)
                .join(AppModel, AppModel.id == AppInstallationModel.app_id)
                .where(AppModel.slug == svc.SLUG)
            )
        )
        .scalars()
        .all()
    )
    return [sid for sid in installed if await is_live(db, sid, svc.SLUG)]


async def recent_changes(
    db: AsyncSession, store_id: UUID, since: datetime
) -> list[Any]:
    """Forward shipments now out for delivery or failed, touched since ``since``."""
    return list(
        (
            await db.execute(
                select(
                    ShipmentModel.order_id,
                    ShipmentModel.status,
                    ShipmentModel.status_history,
                ).where(
                    ShipmentModel.store_id == store_id,
                    FORWARD,
                    ShipmentModel.status.in_(tuple(svc.KIND_OF_STATUS)),
                    ShipmentModel.updated_at >= since,
                )
            )
        ).all()
    )


async def overdue(db: AsyncSession, store_id: UUID, before: datetime) -> list[Message]:
    """Scheduled messages whose send never ran (a lost Celery message)."""
    return list(
        (
            await db.execute(
                select(Message).where(
                    Message.store_id == store_id,
                    Message.status == svc.SCHEDULED,
                    Message.send_at <= before,
                )
            )
        )
        .scalars()
        .all()
    )


async def message_counts(
    db: AsyncSession, store_id: UUID, since: datetime
) -> tuple[int, int, dict[str, int]]:
    rows = (
        await db.execute(
            select(Message.status, Message.skip_reason, func.count())
            .where(Message.store_id == store_id, Message.created_at >= since)
            .group_by(Message.status, Message.skip_reason)
        )
    ).all()
    sent = sum(n for s, _r, n in rows if s == svc.SENT)
    skipped = sum(n for s, _r, n in rows if s == svc.SKIPPED)
    reasons: dict[str, int] = {}
    for s, r, n in rows:
        if s == svc.SKIPPED and r:
            reasons[r] = reasons.get(r, 0) + n
    return sent, skipped, reasons


# ─── Reconcile ─────────────────────────────────────────────────────────

_SHIPPED = (OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.RETURNED)


def shipped_orders(store_id: UUID, since: datetime) -> Select:
    """Orders of the window that shipped: by status, or with a forward
    shipment (bulk updates and the first install never told the handler).
    ponytail: "in the window" is by order date; an order shipped 30+ days
    after it was placed is left out."""
    has_shipment = exists().where(ShipmentModel.order_id == OrderModel.id, FORWARD)
    return select(OrderModel.id).where(
        OrderModel.store_id == store_id,
        OrderModel.created_at >= since,
        or_(OrderModel.status.in_(_SHIPPED), has_shipment),
    )


async def backfill(db: AsyncSession, store_id: UUID, since: datetime) -> dict[str, int]:
    """``done`` of ``total`` shipped orders of the window have a parcel row."""
    ids = shipped_orders(store_id, since).subquery()
    total = await db.scalar(select(func.count()).select_from(ids)) or 0
    done = (
        await db.scalar(
            select(func.count())
            .select_from(ParcelRow)
            .where(ParcelRow.store_id == store_id, ParcelRow.order_id.in_(select(ids)))
        )
        or 0
    )
    return {"done": done, "total": total}


async def untracked(db: AsyncSession, store_id: UUID, since: datetime) -> list[UUID]:
    have = select(ParcelRow.order_id).where(ParcelRow.store_id == store_id)
    return list(
        (
            await db.execute(
                shipped_orders(store_id, since).where(OrderModel.id.notin_(have))
            )
        )
        .scalars()
        .all()
    )


async def expire(db: AsyncSession, now: datetime) -> dict[str, int]:
    """Retention: rows older than ``KEEP`` go; a handled request's note is
    blanked ``NOTE_KEEP`` after handling."""
    old = now - svc.KEEP
    bulk = {"synchronize_session": False}  # a sweep, no loaded rows to update
    out = {}
    for name, model in (
        ("parcels", ParcelRow),
        ("retry_requests", Retry),
        ("messages", Message),
    ):
        result = await db.execute(
            delete(model).where(model.created_at < old).execution_options(**bulk)
        )
        out[name] = result.rowcount or 0
    result = await db.execute(
        update(Retry)
        .where(Retry.note.is_not(None), Retry.handled_at <= now - svc.NOTE_KEEP)
        .values(note=None)
        .execution_options(**bulk)
    )
    out["notes_blanked"] = result.rowcount or 0
    return out


# ─── Purge and shopper data rights ─────────────────────────────────────

_ORDER_TABLES = (ParcelRow, Retry, Message)


async def purge_store(db: AsyncSession, store_id: UUID) -> None:
    """Uninstall + 30 days: nothing of the app stays for this store."""
    for model in (*_ORDER_TABLES, Settings):
        await db.execute(delete(model).where(model.store_id == store_id))


async def customer_order_ids(
    db: AsyncSession, store_id: UUID, customer_id: Any
) -> list:
    return list(
        (
            await db.execute(
                select(OrderModel.id).where(
                    OrderModel.store_id == store_id,
                    OrderModel.customer_id == customer_id,
                )
            )
        )
        .scalars()
        .all()
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


async def export_for(db: AsyncSession, store_id: UUID, order_ids: list) -> dict:
    """What the app keeps about this shopper's orders."""
    if not order_ids:
        return {"parcels": [], "retry_requests": [], "messages": []}

    async def rows(model: Any) -> list[Any]:
        return list(
            (
                await db.execute(
                    select(model).where(
                        model.store_id == store_id, model.order_id.in_(order_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

    return {
        "parcels": [
            {
                "order_id": str(p.order_id),
                "area": svc.area_out(p.governorate_code, p.city),
                "eta_min": _iso(p.eta_min),
                "eta_max": _iso(p.eta_max),
            }
            for p in await rows(ParcelRow)
        ],
        "retry_requests": [
            {
                "order_id": str(r.order_id),
                "day": _iso(r.day),
                "window": r.window,
                "note": r.note,
                "created_at": _iso(r.created_at),
                "handled_at": _iso(r.handled_at),
            }
            for r in await rows(Retry)
        ],
        "messages": [
            {
                "order_id": str(m.order_id),
                "kind": m.kind,
                "attempt": m.attempt,
                "status": m.status,
                "created_at": _iso(m.created_at),
            }
            for m in await rows(Message)
        ],
    }


async def delete_for(db: AsyncSession, store_id: UUID, order_ids: list) -> int:
    if not order_ids:
        return 0
    deleted = 0
    for model in _ORDER_TABLES:
        result = await db.execute(
            delete(model).where(
                model.store_id == store_id, model.order_id.in_(order_ids)
            )
        )
        deleted += result.rowcount or 0
    return deleted

"""``/api/v1/apps/order-tracking/…``: the merchant screens of «مشوار».

Called by the app's front inside the hub with the hub's session token; the
store always comes from the token (``AppSession.store_id``), never from the
request. Shapes: docs/Plans/APPS/02-order-tracking/CONTRACT.md. Nothing here
returns a shopper's phone, email or street, except ``parcels/{id}/phone``,
which returns the phone on purpose and never logs it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.app_session import (
    AppSession,
    app_router,
    require_app_session,
)
from src.api.dependencies.database import get_db
from src.application.services import order_tracking_service as svc
from src.application.services.back_in_stock import normalize_phone, store_host
from src.application.services.back_in_stock_whatsapp import can_send, whatsapp_state
from src.application.services.carrier_resolver import (
    CarrierCapabilityError,
    UnknownCarrierError,
    capability,
    service_for_shipment,
    supports,
    tracking_url_for,
)
from src.core.utils.store_timezone import local_date, resolve_store_timezone_name
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingParcelModel as ParcelRow,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingRetryRequestModel as Retry,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)
from src.infrastructure.repositories import order_tracking_repository as repo

router = app_router(svc.SLUG)

Session = Annotated[AppSession, Depends(require_app_session(svc.SLUG))]
Db = Annotated[AsyncSession, Depends(get_db)]


def _iso(value: Any) -> str | None:
    value = svc.aware(value) if isinstance(value, datetime) else value
    return value.isoformat() if value else None


def _error(code: int, key: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"code": key, "detail": message})


def _eta(eta_min: date | None, eta_max: date | None) -> dict | None:
    return {"min": _iso(eta_min), "max": _iso(eta_max)} if eta_max else None


async def _setup(db: AsyncSession, store: StoreModel) -> dict[str, Any]:
    """The store's settings; the front's first call makes the row and queues
    the 30-day reconcile that fills the board."""
    row, created = await repo.ensure_settings(db, store)
    if created:
        from src.infrastructure.messaging.tasks.order_tracking_tasks import (
            reconcile_task,
        )

        reconcile_task.apply_async(kwargs={"store_id": str(store.id)})
    return svc.merged_settings(row)


async def _whatsapp(db: AsyncSession, store: StoreModel) -> dict[str, Any]:
    states = {
        kind: await whatsapp_state(db, store, template)
        for kind, (_status, template) in svc.KINDS.items()
    }
    first = states[svc.OUT_FOR_DELIVERY]
    return {
        "mode": first["mode"],
        "transport": first["transport"],
        "access": first["access"],
        "access_reason": first["access_reason"],
        "usable": any(can_send(s) for s in states.values()),
        "templates": {
            kind: {lang: s["template"].get(lang) for lang in ("ar", "en")}
            for kind, s in states.items()
        },
    }


def _now_and_today(store: StoreModel) -> tuple[datetime, date, str]:
    tz = resolve_store_timezone_name(store.settings)
    now = datetime.now(UTC)
    return now, local_date(now, tz), tz


def _can_note(carrier: str | None, retry: Retry | None) -> bool:
    """Bosta takes a note; only an open request whose note has not gone yet."""
    return bool(
        carrier
        and supports(carrier, "update_delivery")
        and retry is not None
        and retry.handled_at is None
        and retry.courier_notified_at is None
    )


def _retry_out(r: Retry, *, full: bool = False) -> dict[str, Any]:
    out = {
        "id": str(r.id),
        "day": _iso(r.day),
        "window": r.window,
        "note": r.note,
        "created_at": _iso(r.created_at),
    }
    if full:
        out |= {
            "handled_at": _iso(r.handled_at),
            "courier_notified_at": _iso(r.courier_notified_at),
        }
    return out


async def _row_or_404(
    db: AsyncSession, session: AppSession, order_id: UUID
) -> ParcelRow:
    row = await repo.parcel_row(db, session.store_id, order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


# ─── First run, board, parcels ─────────────────────────────────────────


@router.get("/status")
async def status(session: Session, db: Db) -> dict:
    store = await db.get(StoreModel, session.store_id)
    await _setup(db, store)
    now, _today, _tz = _now_and_today(store)
    progress = await repo.backfill(db, store.id, now - svc.WINDOW)
    newest = await db.scalar(
        select(ParcelRow.order_id)
        .where(ParcelRow.store_id == store.id)
        .order_by(ParcelRow.created_at.desc())
        .limit(1)
    )
    host = store_host(store)
    return {
        "backfill": {**progress, "finished": progress["done"] >= progress["total"]},
        "store": {"name": store.name, "host": host},
        "sample_tracking_url": f"https://{host}/track/{newest}" if newest else None,
        "whatsapp": await _whatsapp(db, store),
    }


@router.get("/board")
async def board(session: Session, db: Db) -> dict:
    store = await db.get(StoreModel, session.store_id)
    await _setup(db, store)
    now, today, tz = _now_and_today(store)
    parcels = await repo.parcels(db, store.id, now - svc.WINDOW)
    retries = await repo.open_retries(db, store.id)
    items = []
    for reason, p in svc.queue(parcels, retries, today):
        retry = retries.get(p.order_id)
        late = svc.is_delayed(p.status, p.eta_max, today)
        items.append({
            "order_id": str(p.order_id),
            "order_number": p.order_number,
            "reason": reason,
            "area": svc.area_out(p.governorate_code, p.city),
            "carrier": svc.carrier_out(p.carrier),
            "status": p.status,
            "attempt": p.attempts,
            "days_late": (today - p.eta_max).days if late else None,
            "retry": _retry_out(retry) if reason == "retry_request" else None,
            "can_courier_note": _can_note(p.carrier, retry),
        })
    return {"counts": svc.board_counts(parcels, today, tz), "queue": items}


@router.get("/parcels")
async def parcels(
    session: Session,
    db: Db,
    status: Literal[
        "in_transit",
        "out_for_delivery",
        "delivered_today",
        "delayed",
        "failed",
        "returned",
    ]
    | None = None,
    q: Annotated[str | None, Query(max_length=50)] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    store = await db.get(StoreModel, session.store_id)
    now, today, tz = _now_and_today(store)
    needle = (q or "").strip().removeprefix("#").casefold()
    rows = [
        p
        for p in await repo.parcels(db, store.id, now - svc.WINDOW)
        if (status is None or svc.in_tile(p, status, today, tz))
        and needle in p.order_number.casefold()
    ]
    rows.sort(key=lambda p: p.updated_at, reverse=True)
    return {
        "items": [
            {
                "order_id": str(p.order_id),
                "order_number": p.order_number,
                "status": p.status,
                "area": svc.area_out(p.governorate_code, p.city),
                "carrier": svc.carrier_out(p.carrier),
                "eta": _eta(p.eta_min, p.eta_max),
                "delayed": svc.is_delayed(p.status, p.eta_max, today),
                "attempts": p.attempts,
                "updated_at": _iso(p.updated_at),
            }
            for p in rows[skip : skip + limit]
        ],
        "total": len(rows),
    }


@router.get("/parcels/{order_id}")
async def parcel(session: Session, db: Db, order_id: UUID) -> dict:
    """The drawer. The carrier's own descriptions never leave the API."""
    row = await _row_or_404(db, session, order_id)
    order = await repo.order(db, order_id)
    shipment = await repo.latest_shipment(db, order_id)
    history = shipment.status_history if shipment else []
    status = svc.parcel_status(shipment.status if shipment else None, order.status)
    store = await db.get(StoreModel, session.store_id)
    _now, today, _tz = _now_and_today(store)
    retry = await repo.newest_retry(db, session.store_id, order_id)
    carrier = shipment.carrier if shipment else None
    order_history = (order.extra_data or {}).get("status_history")
    return {
        "order_id": str(order_id),
        "order_number": order.order_number,
        "status": status,
        "area": svc.area_out(row.governorate_code, row.city),
        "carrier": svc.carrier_out(carrier),
        "tracking_number": shipment.tracking_number if shipment else None,
        "tracking_url": (
            tracking_url_for(carrier, shipment.tracking_number) if shipment else None
        ),
        "eta": _eta(row.eta_min, row.eta_max),
        "delayed": svc.is_delayed(status, row.eta_max, today),
        "attempts": svc.entries(history, "failed"),
        "steps": svc.merchant_steps(
            order.status, svc.entered_at(order_history, "confirmed"), history
        ),
        "retry": _retry_out(retry, full=True) if retry else None,
        "can_courier_note": _can_note(carrier, retry),
    }


@router.get("/parcels/{order_id}/phone")
async def phone(session: Session, db: Db, order_id: UUID) -> dict:
    """The shopper's phone, read live when the merchant clicks; never stored
    by the app and never logged."""
    await _row_or_404(db, session, order_id)
    order = await repo.order(db, order_id)
    raw = (order.shipping_address or {}).get("phone") or await db.scalar(
        select(CustomerModel.phone).where(CustomerModel.id == order.customer_id)
    )
    return {"phone": (normalize_phone(raw) or raw) if raw else None}


# ─── Retry requests ────────────────────────────────────────────────────


@router.post("/parcels/{order_id}/courier-note")
async def courier_note(session: Session, db: Db, order_id: UUID):
    """The open request's day, window and note, appended to the shipment's
    note on Bosta. ``notes`` only; once per request."""
    await _row_or_404(db, session, order_id)
    shipment = await repo.latest_shipment(db, order_id)
    if (
        shipment is None
        or not shipment.tracking_number
        or not supports(shipment.carrier, "update_delivery")
    ):
        return _error(409, "carrier_unsupported", "This carrier takes no notes.")
    # One click at a time per order, so the note goes to Bosta once.
    await repo.lock(db, f"order-tracking-retry:{order_id}")
    retry = await repo.newest_retry(db, session.store_id, order_id)
    if retry is None or retry.handled_at is not None:
        return _error(409, "no_open_request", "There is no open request.")
    if retry.courier_notified_at is not None:
        return _error(409, "already_sent", "This request already went to the courier.")
    store = await db.get(StoreModel, session.store_id)
    try:
        service = await service_for_shipment(shipment, store.settings or {})
        update_delivery = capability(service, "update_delivery", shipment.carrier)
    except (UnknownCarrierError, CarrierCapabilityError):
        return _error(409, "carrier_unsupported", "This carrier takes no notes.")
    notes = svc.courier_note(
        (shipment.extra_data or {}).get("notes"), retry.day, retry.window, retry.note
    )
    try:
        await update_delivery(shipment.tracking_number, notes=notes)
    except Exception:  # noqa: BLE001 — the carrier refused; nothing is marked sent
        return _error(502, "carrier_error", "The courier did not take the note.")
    shipment.extra_data = {**(shipment.extra_data or {}), "notes": notes}
    retry.courier_notified_at = datetime.now(UTC)
    return {"status": "sent"}


@router.post("/parcels/{order_id}/handled")
async def handled(session: Session, db: Db, order_id: UUID):
    await _row_or_404(db, session, order_id)
    result = await db.execute(
        update(Retry)
        .where(
            Retry.store_id == session.store_id,
            Retry.order_id == order_id,
            Retry.handled_at.is_(None),
        )
        .values(handled_at=datetime.now(UTC))
    )
    if not result.rowcount:
        return _error(409, "no_open_request", "There is no open request.")
    return {"status": "handled"}


# ─── Analytics, messages, settings ─────────────────────────────────────


@router.get("/analytics")
async def analytics(session: Session, db: Db) -> dict:
    now = datetime.now(UTC)
    return svc.analytics(await repo.parcels(db, session.store_id, now - svc.WINDOW))


_SAMPLE_NAME = {"ar": "منى", "en": "Mona"}


@router.get("/messages")
async def messages(session: Session, db: Db) -> dict:
    from src.core.interfaces.services.messaging_service import MessageType
    from src.core.whatsapp_plain_render import render_plain_template
    from src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher import (
        _resolve_language,
    )

    store = await db.get(StoreModel, session.store_id)
    cfg = await _setup(db, store)
    wa = await _whatsapp(db, store)
    rejected = dict(
        (
            await db.execute(
                select(
                    WhatsAppTemplateModel.name, WhatsAppTemplateModel.rejection_reason
                ).where(
                    WhatsAppTemplateModel.store_id == store.id,
                    WhatsAppTemplateModel.name.in_([t for _s, t in svc.KINDS.values()]),
                    WhatsAppTemplateModel.status == "REJECTED",
                )
            )
        ).all()
    )
    now = datetime.now(UTC)
    sent, skipped, reasons = await repo.message_counts(db, store.id, now - svc.WINDOW)
    lang = _resolve_language(store.settings or {}, store.default_language)

    def kind_out(kind: str) -> dict[str, Any]:
        return {
            "enabled": cfg[svc.SWITCH[kind]],
            "template": wa["templates"][kind],
            "rejected_reason": rejected.get(svc.KINDS[kind][1]),
        }

    def preview(kind: str) -> str | None:
        params = svc.template_params(
            _SAMPLE_NAME[lang],
            "1042",
            store.name,
            svc.button_value(store.subdomain, "…"),
            lang,
        )
        rendered = render_plain_template(MessageType(kind), lang, params)
        return rendered.text if rendered else None

    return {
        "whatsapp": {
            k: wa[k] for k in ("usable", "access_reason", "mode", "transport")
        },
        svc.OUT_FOR_DELIVERY: kind_out(svc.OUT_FOR_DELIVERY),
        svc.DELIVERY_FAILED: kind_out(svc.DELIVERY_FAILED),
        "sent_30d": sent,
        "skipped_30d": skipped,
        "skip_reasons": reasons,
        "daily_cap": cfg["daily_cap"],
        "language": lang,
        "preview": {kind: preview(kind) for kind in svc.KINDS},
    }


@router.get("/settings")
async def get_settings(session: Session, db: Db) -> dict:
    return await _setup(db, await db.get(StoreModel, session.store_id))


class SettingsIn(BaseModel):
    """Any subset; ``daily_cap`` 0–5000."""

    model_config = ConfigDict(extra="forbid")

    out_for_delivery_enabled: bool | None = None
    failed_enabled: bool | None = None
    daily_cap: int | None = Field(default=None, ge=0, le=svc.CAP_MAX)
    show_items: bool | None = None
    show_contact: bool | None = None


@router.put("/settings")
async def put_settings(session: Session, db: Db, body: SettingsIn) -> dict:
    store = await db.get(StoreModel, session.store_id)
    await _setup(db, store)
    row = await repo.settings_row(db, store.id)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(row, key, value)
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return svc.merged_settings(row)

"""Order Tracking app «مشوار»: the shopper routes behind the host's /track page.

``/storefront/store/{store_id}/apps/order-tracking/…`` is called by the
store's own host relay (``/api/apps/order-tracking/…``), which adds the
internal-service headers, so the per-shopper rate limits apply. The order
UUID is the secret, as on core's tracking page.

Every answer is ``Cache-Control: private, no-store`` and never carries a
phone, email, street or carrier description. An unknown order, another
store's order and an order the shopper cannot see yet all answer the same
404; a store without the app answers 404 "not installed", and the host then
shows core's own view.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.application.services import order_tracking_service as svc
from src.application.services.app_install_gate import is_live
from src.application.services.carrier_resolver import tracking_url_for
from src.core.entities.order_activity import OrderActivityKind
from src.core.utils.store_timezone import local_date, resolve_store_timezone_name
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.order_activity import (
    OrderActivityModel,
)
from src.infrastructure.database.models.tenant.order_tracking import (
    OrderTrackingRetryRequestModel as Retry,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories import order_tracking_repository as repo

router = APIRouter(prefix=f"/apps/{svc.SLUG}", tags=["Storefront - Order Tracking"])

StoreId = Annotated[UUID, Path(description="Store ID")]
Db = Annotated[AsyncSession, Depends(get_db)]

NO_STORE = {"Cache-Control": "private, no-store"}


def _json(code: int, content: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=code, content=content, headers=NO_STORE)


async def _visible(
    db: AsyncSession, store_id: UUID, order_id: UUID
) -> tuple[StoreModel, OrderModel]:
    store = await db.get(StoreModel, store_id)
    if store is None or not await is_live(db, store_id, svc.SLUG):
        raise HTTPException(status_code=404, detail="not installed", headers=NO_STORE)
    order = await repo.order(db, order_id)
    if order is None or order.store_id != store_id or svc.hidden(order.status):
        raise HTTPException(status_code=404, detail="not found", headers=NO_STORE)
    return store, order


def _today(store: StoreModel) -> tuple[datetime, date, str]:
    tz = resolve_store_timezone_name(store.settings)
    now = datetime.now(UTC)
    return now, local_date(now, tz), tz


@router.get("/orders/{order_id}")
async def order_view(
    store_id: StoreId, order_id: UUID, response: Response, db: Db
) -> dict:
    """The app's view of one order: DESIGN § 3, CONTRACT ``GET orders``."""
    store, order = await _visible(db, store_id, order_id)
    response.headers.update(NO_STORE)
    now, today, tz = _today(store)
    cfg = await repo.settings(db, store_id)
    shipment = await repo.latest_shipment(db, order_id)
    history = shipment.status_history if shipment else []
    order_history = (order.extra_data or {}).get("status_history")
    status = svc.parcel_status(shipment.status if shipment else None, order.status)

    row = await repo.parcel_row(db, store_id, order_id)
    if row is not None:
        code, city, eta_min, eta_max = (
            row.governorate_code,
            row.city,
            row.eta_min,
            row.eta_max,
        )
    else:  # not shipped yet: the same rules, read live
        code, city = svc.area_of(order.shipping_address)
        eta_min, eta_max = await repo.window_for(db, order, tz)
    area = svc.area_out(code, city)
    if area is not None:
        area.pop("governorate_code")
    retried = await repo.retried_since(db, store_id, order_id, now - svc.RETRY_EVERY)
    delivered_at = (shipment.delivered_at if shipment else None) or (
        svc.entered_at(order_history, "delivered") if status == "delivered" else None
    )
    carrier = shipment.carrier if shipment else None

    out: dict[str, Any] = {
        "order_number": order.order_number,
        "status": status,
        "steps": svc.shopper_steps(
            status, svc.entered_at(order_history, "confirmed"), history
        ),
        "attempt": svc.entries(history, "failed") if status == "failed" else None,
        "eta": {"min": eta_min.isoformat(), "max": eta_max.isoformat()}
        if eta_min and eta_max
        else None,
        "delayed": svc.is_delayed(status, eta_max, today),
        "delivered_at": svc.aware(delivered_at).isoformat() if delivered_at else None,
        "carrier": svc.carrier_out(carrier),
        "tracking_number": shipment.tracking_number if shipment else None,
        "tracking_url": (
            tracking_url_for(carrier, shipment.tracking_number) if shipment else None
        ),
        "area": area,
        "retry": {"open": status == "failed" and not retried, "sent": retried},
        "show_items": cfg["show_items"],
        "show_contact": cfg["show_contact"],
    }
    if cfg["show_items"]:
        out["items"] = [
            {
                "name": li.get("product_name"),
                "variant": li.get("variant_name"),
                "quantity": li.get("quantity"),
            }
            for li in order.line_items or []
        ]
        out["total"] = {"amount": order.total, "currency": order.currency}
    if cfg["show_contact"]:
        # The store's own numbers; the host turns them into wa.me / tel: links.
        out["contact"] = {
            "whatsapp": str((store.social_links or {}).get("whatsapp") or "").strip()
            or None,
            "phone": (store.contact_phone or "").strip() or None,
        }
    return out


class RetryIn(BaseModel):
    """ "Try again": a day in the next three, a window, an optional note."""

    model_config = ConfigDict(extra="forbid")

    day: date
    window: Literal["morning", "afternoon", "evening"]
    note: str | None = Field(default=None, max_length=svc.NOTE_MAX)

    @field_validator("note")
    @classmethod
    def no_control_characters(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if svc.CONTROL.search(value):
            raise ValueError("The note has control characters.")
        return value.strip() or None


@router.post("/orders/{order_id}/retry")
async def retry(store_id: StoreId, order_id: UUID, body: RetryIn, db: Db):
    store, order = await _visible(db, store_id, order_id)
    now, today, _tz = _today(store)
    if not svc.retry_day_ok(body.day, today):
        return _json(422, {"detail": "The day must be within the next 3 days."})
    # Two taps at once: the second waits, then sees the first.
    await repo.lock(db, f"order-tracking-retry:{order_id}")
    shipment = await repo.latest_shipment(db, order_id)
    status = svc.parcel_status(shipment.status if shipment else None, order.status)
    if status != "failed":
        return _json(409, {"code": "not_failed", "detail": "Delivery has not failed."})
    if await repo.retried_since(db, store_id, order_id, now - svc.RETRY_EVERY):
        return _json(409, {"code": "already_sent", "detail": "Already sent."})
    db.add(
        Retry(
            store_id=store_id,
            tenant_id=order.tenant_id,
            order_id=order_id,
            day=body.day,
            window=body.window,
            note=body.note,
        )
    )
    db.add(
        OrderActivityModel(
            tenant_id=order.tenant_id,
            store_id=store_id,
            order_id=order_id,
            user_id=None,
            kind=OrderActivityKind.SYSTEM_EVENT,
            event_type="order_tracking_retry",
            body=svc.retry_comment(body.day, body.window, body.note),
            activity_metadata={
                "app": svc.SLUG,
                "day": body.day.isoformat(),
                "window": body.window,
            },
        )
    )
    await db.flush()
    return _json(
        200, {"status": "sent", "day": body.day.isoformat(), "window": body.window}
    )

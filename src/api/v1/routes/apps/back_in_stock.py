"""``/api/v1/apps/back-in-stock/…``: the merchant screens of the Back in Stock app.

Called by the app's front inside the hub with the hub's session token; the
store always comes from the token (``AppSession.store_id``), never from the
request. Contacts leave this API masked only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.app_session import (
    AppSession,
    app_router,
    require_app_session,
)
from src.api.dependencies.database import get_db
from src.application.services import back_in_stock as bis
from src.application.services.back_in_stock_whatsapp import (
    can_send,
    whatsapp_state,
)
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.back_in_stock import (
    BackInStockSettingsModel,
)
from src.infrastructure.database.models.tenant.back_in_stock import (
    BackInStockWaiterModel as Waiter,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.theme import (
    StoreThemeModel,
    ThemeVersionModel,
)
from src.infrastructure.repositories import back_in_stock_repository as repo

router = app_router(bis.SLUG)

Session = Annotated[AppSession, Depends(require_app_session(bis.SLUG))]
Db = Annotated[AsyncSession, Depends(get_db)]

WINDOW = timedelta(days=30)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@router.get("/status")
async def status(session: Session, db: Db) -> dict:
    """First run: WhatsApp, the theme's button, the sign-up choice."""
    store = await db.get(StoreModel, session.store_id)
    state = await whatsapp_state(db, store)
    theme = (
        await db.execute(
            select(ThemeVersionModel.manifest)
            .join(
                StoreThemeModel,
                StoreThemeModel.theme_version_id == ThemeVersionModel.id,
            )
            .where(
                StoreThemeModel.store_id == session.store_id,
                StoreThemeModel.is_active.is_(True),
            )
        )
    ).scalar_one_or_none() or {}
    slug, version = theme.get("id"), theme.get("version")
    return {
        "whatsapp": {**state, "usable": can_send(state)},
        "theme": {
            "slug": slug,
            "version": version,
            "ready": bis.theme_ready(slug, version),
        },
        "settings": await repo.settings(db, session.store_id),
    }


@router.get("/overview")
async def overview(session: Session, db: Db) -> dict:
    since = datetime.now(UTC) - WINDOW
    store_id = session.store_id
    revenue = await db.scalar(
        select(func.coalesce(func.sum(Waiter.revenue), 0)).where(
            Waiter.store_id == store_id, Waiter.purchased_at >= since
        )
    )
    return {
        "waiting": await repo.count(
            db, Waiter.store_id == store_id, Waiter.status == bis.WAITING
        ),
        "alerted_30d": await repo.count(
            db, Waiter.store_id == store_id, Waiter.notified_at >= since
        ),
        "clicked_30d": await repo.count(
            db, Waiter.store_id == store_id, Waiter.clicked_at >= since
        ),
        "bought_30d": await repo.count(
            db, Waiter.store_id == store_id, Waiter.purchased_at >= since
        ),
        "revenue_30d": int(revenue or 0),
    }


@router.get("/waitlist")
async def waitlist(session: Session, db: Db, limit: int = 50) -> dict:
    """Most wanted: one row per product and variant that has waiters."""
    is_waiting = Waiter.status == bis.WAITING
    rows = (
        await db.execute(
            select(
                Waiter.product_id,
                Waiter.variant_id,
                func.max(Waiter.product_title).label("product"),
                func.max(Waiter.variant_title).label("variant"),
                func.count().filter(is_waiting).label("waiting"),
                func.min(Waiter.created_at).filter(is_waiting).label("oldest"),
                func.max(Waiter.notified_at).label("last_alert"),
                func.count(Waiter.purchased_at).label("bought"),
            )
            .where(Waiter.store_id == session.store_id)
            .group_by(Waiter.product_id, Waiter.variant_id)
            .having(func.count().filter(is_waiting) > 0)
            .order_by(func.count().filter(is_waiting).desc())
            .limit(min(max(limit, 1), 200))
        )
    ).all()
    return {
        "items": [
            {
                "product_id": str(r.product_id),
                "variant_id": str(r.variant_id) if r.variant_id else None,
                "product": r.product,
                "variant": r.variant,
                "waiting": r.waiting,
                "oldest": _iso(r.oldest),
                "last_alert": _iso(r.last_alert),
                "bought": r.bought,
            }
            for r in rows
        ]
    }


def _waiter_out(w: Waiter) -> dict:
    return {
        "id": str(w.id),
        "contact": bis.mask(w.contact),
        "channel": w.channel,
        "status": w.status,
        "created_at": _iso(w.created_at),
        "notified_at": _iso(w.notified_at),
        "fail_reason": w.fail_reason,
    }


@router.get("/waitlist/{product_id}")
async def waiters(
    session: Session, db: Db, product_id: UUID, variant_id: UUID | None = None
) -> dict:
    stmt = select(Waiter).where(
        Waiter.store_id == session.store_id, Waiter.product_id == product_id
    )
    if variant_id is not None:
        stmt = stmt.where(Waiter.variant_id == variant_id)
    rows = (
        (await db.execute(stmt.order_by(Waiter.created_at).limit(500))).scalars().all()
    )
    return {"items": [_waiter_out(w) for w in rows]}


@router.delete("/waiters/{waiter_id}")
async def delete_waiter(session: Session, db: Db, waiter_id: UUID) -> dict:
    """A shopper asked the merchant to remove them: the row goes."""
    result = await db.execute(
        delete(Waiter).where(
            Waiter.id == waiter_id, Waiter.store_id == session.store_id
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": str(waiter_id)}


class NotifyNow(BaseModel):
    product_id: UUID
    variant_id: UUID | None = None


@router.post("/notify-now")
async def notify_now(session: Session, db: Db, body: NotifyNow) -> dict:
    product, variants = await repo.product_with_variants(
        db, session.store_id, body.product_id
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if not bis.is_buyable(product, variants, body.variant_id):
        raise HTTPException(status_code=409, detail="This variant is not in stock yet.")
    from src.infrastructure.messaging.tasks.back_in_stock_app_tasks import (
        restock_check_task,
    )

    restock_check_task.apply_async(args=[str(session.store_id), str(body.product_id)])
    return {"status": "queued"}


@router.get("/activity")
async def activity(session: Session, db: Db, limit: int = 50) -> dict:
    rows = (
        (
            await db.execute(
                select(Waiter)
                .where(
                    Waiter.store_id == session.store_id,
                    Waiter.status.in_((bis.QUEUED, bis.NOTIFIED, bis.FAILED)),
                )
                .order_by(Waiter.updated_at.desc())
                .limit(min(max(limit, 1), 200))
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                **_waiter_out(w),
                "product": w.product_title,
                "variant": w.variant_title,
                "at": _iso(w.notified_at or w.updated_at),
            }
            for w in rows
        ]
    }


@router.get("/settings")
async def get_settings(session: Session, db: Db) -> dict:
    return await repo.settings(db, session.store_id)


class SettingsIn(BaseModel):
    contact: Literal["phone_or_email", "phone", "email"]
    signup_cap: int = Field(ge=1, le=bis.CAP_LIMITS["signup_cap"])
    wa_cap: int = Field(ge=1, le=bis.CAP_LIMITS["wa_cap"])
    email_cap: int = Field(ge=1, le=bis.CAP_LIMITS["email_cap"])


@router.put("/settings")
async def put_settings(session: Session, db: Db, body: SettingsIn) -> dict:
    row = await repo.settings_row(db, session.store_id)
    if row is None:
        store = await db.get(StoreModel, session.store_id)
        row = BackInStockSettingsModel(
            store_id=session.store_id, tenant_id=store.tenant_id
        )
        db.add(row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return bis.merged_settings(row)


class TestSend(BaseModel):
    channel: Literal["whatsapp", "email"]
    product_id: UUID


@router.post("/test")
async def test_send(session: Session, db: Db, body: TestSend) -> dict:
    """The real message, sent only to the signed-in merchant's own contact."""
    user = await db.get(UserModel, session.user_id)
    contact = (
        bis.normalize_phone(user.phone)
        if body.channel == "whatsapp"
        else bis.normalize_email(user.email)
    )
    if contact is None:
        raise HTTPException(
            status_code=422, detail=f"Your account has no {body.channel} contact."
        )
    product, _variants = await repo.product_with_variants(
        db, session.store_id, body.product_id
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    store = await db.get(StoreModel, session.store_id)
    from src.infrastructure.messaging.tasks.back_in_stock_app_tasks import send_test

    message_id, reason = await send_test(
        db,
        store,
        channel=body.channel,
        contact=contact,
        product=product,
        locale=session.locale,
    )
    return {"sent": message_id is not None, "to": bis.mask(contact), "reason": reason}

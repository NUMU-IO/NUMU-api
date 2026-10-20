"""Reviews app: the queries its routes, handler and tasks share.

The reviews are core ``product_reviews`` rows; this module adds the app's own
requests and settings, and the merchant's view of the core rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services import reviews_service as svc
from src.application.services.app_install_gate import is_live
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
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.product_review import (
    ProductReviewModel as Review,
)
from src.infrastructure.database.models.tenant.reviews import (
    ReviewsRequestModel as Request,
)
from src.infrastructure.database.models.tenant.reviews import (
    ReviewsSettingsModel as Settings,
)
from src.infrastructure.repositories.order_tracking_repository import _insert, lock

# ─── Settings ──────────────────────────────────────────────────────────


async def settings_row(db: AsyncSession, store_id: UUID) -> Settings | None:
    return await db.get(Settings, store_id)


async def settings(db: AsyncSession, store_id: UUID) -> dict[str, Any]:
    return svc.merged_settings(await settings_row(db, store_id))


async def ensure_settings(db: AsyncSession, store: Any) -> Settings:
    """The store's row, made with the defaults the first time. Two screens
    can ask at once, so the insert does nothing if the other one won."""
    row = await settings_row(db, store.id)
    if row is None:
        await db.execute(
            _insert(db, Settings)
            .values(
                store_id=store.id, tenant_id=store.tenant_id, **svc.DEFAULT_SETTINGS
            )
            .on_conflict_do_nothing(index_elements=["store_id"])
        )
        row = await settings_row(db, store.id)
    return row


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


# ─── Requests ──────────────────────────────────────────────────────────


async def schedule(
    db: AsyncSession, store: Any, order_id: UUID, delivered: datetime
) -> Request | None:
    """The order's one request: ``scheduled`` for its send time, or
    ``skipped`` with the switch off. None when the order has one already."""
    cfg = await settings(db, store.id)
    tz = resolve_store_timezone_name(store.settings)
    status, reason = svc.SCHEDULED, None
    if not cfg["requests_enabled"]:
        status, reason = svc.SKIPPED, "switch_off"
    new_id = await db.scalar(
        _insert(db, Request)
        .values(
            id=uuid4(),
            store_id=store.id,
            tenant_id=store.tenant_id,
            order_id=order_id,
            status=status,
            skip_reason=reason,
            due_at=svc.due_at(delivered, cfg["delay_days"], tz),
            created_at=delivered,
            updated_at=delivered,
        )
        .on_conflict_do_nothing(index_elements=["store_id", "order_id"])
        .returning(Request.id)
    )
    return await db.get(Request, new_id) if new_id else None


async def call_off(db: AsyncSession, order_id: UUID, now: datetime) -> int:
    """A returned, refunded or cancelled order: its waiting request goes."""
    result = await db.execute(
        update(Request)
        .where(Request.order_id == order_id, Request.status == svc.SCHEDULED)
        .values(status=svc.CANCELLED, skip_reason="order_closed", updated_at=now)
    )
    return result.rowcount or 0


async def due(db: AsyncSession, store_id: UUID, now: datetime, limit: int) -> list:
    return list(
        (
            await db.execute(
                select(Request.id)
                .where(
                    Request.store_id == store_id,
                    Request.status == svc.SCHEDULED,
                    Request.due_at <= now,
                    Request.updated_at <= now - svc.LEASE,
                )
                .order_by(Request.due_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def in_flight(db: AsyncSession, store_id: UUID, now: datetime) -> int:
    """Requests queued and leased but not sent yet: they count against the
    daily cap like sent ones, or slow sends would let the next sweeps overshoot."""
    return (
        await db.scalar(
            select(func.count())
            .select_from(Request)
            .where(
                Request.store_id == store_id,
                Request.status == svc.SCHEDULED,
                Request.due_at <= now,
                Request.updated_at > now - svc.LEASE,
            )
        )
        or 0
    )


async def lease(db: AsyncSession, ids: list, now: datetime) -> None:
    """Queued now: no sweep queues these again until the lease runs out."""
    if ids:
        await db.execute(
            update(Request).where(Request.id.in_(ids)).values(updated_at=now)
        )


async def sent_today(db: AsyncSession, store: Any, now: datetime) -> int:
    """Requests sent during the store's day, for the daily cap."""
    tz = resolve_store_timezone_name(store.settings)
    start, end = local_day_bounds(local_date(now, tz), tz)
    return (
        await db.scalar(
            select(func.count())
            .select_from(Request)
            .where(
                Request.store_id == store.id,
                Request.sent_at >= start,
                Request.sent_at < end,
            )
        )
        or 0
    )


async def by_token(db: AsyncSession, token: str) -> Request | None:
    return (
        await db.execute(
            select(Request).where(Request.token_hash == svc.token_hash(token))
        )
    ).scalar_one_or_none()


async def cap_lock(db: AsyncSession, store_id: UUID) -> None:
    await lock(db, f"reviews-cap:{store_id}")


async def add_review(db: AsyncSession, **values: Any) -> UUID | None:
    """One buyer review, unless this order already reviewed this product
    (another post of the same link won): then None."""
    return await db.scalar(
        _insert(db, Review)
        .values(id=uuid4(), **values)
        .on_conflict_do_nothing(
            index_elements=["store_id", "order_id", "product_id"],
            index_where=Review.order_id.is_not(None),
        )
        .returning(Review.id)
    )


# ─── The order's products ──────────────────────────────────────────────


async def order_items(db: AsyncSession, order: OrderModel) -> list[dict[str, Any]]:
    """Each product of the order once, if it still exists, and whether this
    order already reviewed it."""
    ids: list[UUID] = []
    for line in order.line_items or []:
        try:
            pid = UUID(str(line.get("product_id")))
        except ValueError:
            continue
        if pid not in ids:
            ids.append(pid)
    if not ids:
        return []
    products = {
        p.id: p
        for p in (
            await db.execute(
                select(ProductModel).where(
                    ProductModel.id.in_(ids), ProductModel.store_id == order.store_id
                )
            )
        )
        .scalars()
        .all()
    }
    done = set(
        (
            await db.execute(
                select(Review.product_id).where(
                    Review.store_id == order.store_id, Review.order_id == order.id
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "product_id": str(pid),
            "name": products[pid].name,
            "image": (products[pid].images or [None])[0],
            "reviewed": pid in done,
        }
        for pid in ids
        if pid in products
    ]


# ─── Reviews, the merchant's view ──────────────────────────────────────

_TAB_FILTER = {
    "pending": (Review.is_approved.is_(False), Review.rejected_reason.is_(None)),
    "published": (Review.is_approved.is_(True),),
    "rejected": (
        Review.is_approved.is_(False),
        Review.rejected_reason.is_not(None),
        Review.rejected_reason != "spam",
    ),
    "spam": (Review.is_approved.is_(False), Review.rejected_reason == "spam"),
}


async def inbox(
    db: AsyncSession,
    store_id: UUID,
    *,
    tab: str,
    rating: int | None = None,
    verified: bool | None = None,
    photos: bool | None = None,
    product_id: UUID | None = None,
    page: int = 1,
    size: int = 25,
) -> tuple[list[Any], int]:
    where = [Review.store_id == store_id, *_TAB_FILTER[tab]]
    if rating is not None:
        where.append(Review.rating == rating)
    if verified is not None:
        where.append(Review.verified.is_(verified))
    if photos is not None:
        where.append(Review.media.is_not(None) if photos else Review.media.is_(None))
    if product_id is not None:
        where.append(Review.product_id == product_id)
    total = await db.scalar(select(func.count()).select_from(Review).where(*where))
    rows = (
        await db.execute(
            select(
                Review, ProductModel.name, ProductModel.images, OrderModel.order_number
            )
            .join(ProductModel, ProductModel.id == Review.product_id)
            .outerjoin(OrderModel, OrderModel.id == Review.order_id)
            .where(*where)
            .order_by(Review.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return list(rows), total or 0


async def review(db: AsyncSession, store_id: UUID, review_id: UUID) -> Review | None:
    row = await db.get(Review, review_id)
    return row if row is not None and row.store_id == store_id else None


async def tab_counts(db: AsyncSession, store_id: UUID) -> dict[str, int]:
    return {
        tab: await db.scalar(
            select(func.count())
            .select_from(Review)
            .where(Review.store_id == store_id, *where)
        )
        or 0
        for tab, where in _TAB_FILTER.items()
    }


async def kpis(db: AsyncSession, store_id: UUID, now: datetime) -> dict[str, Any]:
    """Published reviews and the requests of the last 90 days."""
    published = (Review.store_id == store_id, Review.is_approved.is_(True))
    count, average, with_photos = (
        await db.execute(
            select(
                func.count(),
                func.avg(Review.rating),
                func.count(Review.media),
            ).where(*published)
        )
    ).one()
    since = now - timedelta(days=90)
    sent, completed = (
        await db.execute(
            select(
                func.count(Request.sent_at),
                func.count(Request.completed_at),
            ).where(Request.store_id == store_id, Request.created_at >= since)
        )
    ).one()
    return {
        "published": count or 0,
        "average": round(float(average), 1) if average is not None else None,
        "with_photos": round(with_photos / count, 3) if count else None,
        "requests_sent_90d": sent or 0,
        "conversion_90d": round(completed / sent, 3) if sent else None,
    }


async def requests_page(
    db: AsyncSession, store_id: UUID, *, page: int = 1, size: int = 25
) -> tuple[list[Any], int]:
    total = await db.scalar(
        select(func.count()).select_from(Request).where(Request.store_id == store_id)
    )
    rows = (
        await db.execute(
            select(Request, OrderModel.order_number)
            .join(OrderModel, OrderModel.id == Request.order_id)
            .where(Request.store_id == store_id)
            .order_by(Request.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return list(rows), total or 0


# ─── Reviews, the shopper's view ───────────────────────────────────────


async def published_page(
    db: AsyncSession, store_id: UUID, product_id: UUID, *, page: int, size: int
) -> list[Review]:
    return list(
        (
            await db.execute(
                select(Review)
                .where(
                    Review.store_id == store_id,
                    Review.product_id == product_id,
                    Review.is_approved.is_(True),
                )
                .order_by(Review.created_at.desc())
                .offset((page - 1) * size)
                .limit(size + 1)
            )
        )
        .scalars()
        .all()
    )


async def summary(db: AsyncSession, store_id: UUID, product_id: UUID) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(Review.rating, func.count())
            .where(
                Review.store_id == store_id,
                Review.product_id == product_id,
                Review.is_approved.is_(True),
            )
            .group_by(Review.rating)
        )
    ).all()
    distribution = dict.fromkeys(range(1, 6), 0)
    for rating, n in rows:
        if 1 <= rating <= 5:
            distribution[int(rating)] = int(n)
    count = sum(distribution.values())
    total = sum(r * n for r, n in distribution.items())
    return {
        "average": round(total / count, 1) if count else 0.0,
        "count": count,
        "distribution": {str(r): n for r, n in distribution.items()},
    }


async def payload_section(
    db: AsyncSession, store_id: UUID, product_id: UUID
) -> dict[str, Any] | None:
    """``apps.reviews`` on the product detail payload, when the app is live
    and the product has a published review."""
    if not await is_live(db, store_id, svc.SLUG):
        return None
    s = await summary(db, store_id, product_id)
    if not s["count"]:
        return None
    return {"average": s["average"], "count": s["count"]}


# ─── Uninstall and data rights ─────────────────────────────────────────


def media_prefix(store_id: UUID) -> str:
    return f"reviews/{store_id}/"


async def purge_store(db: AsyncSession, store_id: UUID) -> None:
    """The app's rows, and what it added to core rows; the published text
    reviews stay, as the merchant's own records. Photos go with the prefix."""
    await db.execute(delete(Request).where(Request.store_id == store_id))
    await db.execute(delete(Settings).where(Settings.store_id == store_id))
    await db.execute(
        update(Review)
        .where(
            Review.store_id == store_id,
            or_(Review.reply_body.is_not(None), Review.media.is_not(None)),
        )
        .values(reply_body=None, replied_at=None, media=None)
    )
    try:
        from src.api.dependencies.services import get_storage_service

        storage = get_storage_service()
        for item in await storage.list_files(media_prefix(store_id)):
            await storage.delete_file(item["key"])
    except Exception:  # noqa: BLE001 — the rows are gone; a stray file is not
        pass


def _iso(value: Any) -> str | None:
    value = svc.aware(value)
    return value.isoformat() if value else None


async def export_for(db: AsyncSession, store_id: UUID, order_ids: list) -> list:
    if not order_ids:
        return []
    rows = (
        await db.execute(
            select(Request).where(
                Request.store_id == store_id, Request.order_id.in_(order_ids)
            )
        )
    ).scalars()
    return [
        {
            "order_id": str(r.order_id),
            "status": r.status,
            "sent_at": _iso(r.sent_at),
            "opened_at": _iso(r.opened_at),
            "completed_at": _iso(r.completed_at),
        }
        for r in rows
    ]


async def delete_for(db: AsyncSession, store_id: UUID, order_ids: list) -> int:
    if not order_ids:
        return 0
    result = await db.execute(
        delete(Request).where(
            Request.store_id == store_id, Request.order_id.in_(order_ids)
        )
    )
    return result.rowcount or 0

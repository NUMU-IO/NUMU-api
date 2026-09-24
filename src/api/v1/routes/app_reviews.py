"""App reviews: one rating per store per app, one public partner reply.

- Merchants: ``/stores/{store_id}/apps/{slug}/reviews`` (read, write their
  own, report). Writing needs the app installed for 7 days, now or before.
- Partners: ``/partners/me/reviews`` (their apps' reviews, reply, report).
- Admin: ``/admin/app-reviews`` (reported queue, hide / unhide / dismiss).

Hidden reviews leave every listing and the aggregate, except for their
author, the partner and the admin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.auth import get_current_user_id, require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.partners import (
    require_approved_partner,
    require_partner_program,
)
from src.api.dependencies.services import get_email_service
from src.api.responses import SuccessResponse
from src.api.v1.routes.app_support import (
    partner_email,
    partners_url,
    send_bilingual,
    write_budget,
)
from src.application.services import admin_notifications
from src.core.entities.store import Store
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppReviewModel,
    AppUninstallEventModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

merchant_router = APIRouter(
    prefix="/{store_id}/apps/{slug}/reviews",
    tags=["App Reviews"],
    dependencies=[Depends(verify_store_ownership)],
)
partner_router = APIRouter(
    prefix="/partners/me/reviews",
    tags=["Partner Portal"],
    dependencies=[Depends(require_partner_program)],
)
admin_router = APIRouter()

MIN_INSTALLED = timedelta(days=7)
WRITES_PER_HOUR = 20


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    body: str | None = Field(default=None, max_length=2000)


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class ReportIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ModerateIn(BaseModel):
    action: Literal["hide", "unhide", "dismiss"]


class ReviewOut(BaseModel):
    id: UUID
    app_id: UUID
    app_name: str | None = None
    store_name: str | None
    rating: int
    body: str | None
    reply_body: str | None
    replied_at: datetime | None
    is_hidden: bool
    reported: bool
    report_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class RatingSummary(BaseModel):
    average: float | None
    count: int
    distribution: dict[int, int]


class ReviewPage(BaseModel):
    summary: RatingSummary
    items: list[ReviewOut]
    total: int
    page: int
    page_size: int
    #: Merchant listing only: this store's own review and whether it may write.
    mine: ReviewOut | None = None
    can_review: bool = False


def _out(review: AppReviewModel, store_name, app_name=None, *, staff=False):
    return ReviewOut(
        id=review.id,
        app_id=review.app_id,
        app_name=app_name,
        store_name=store_name,
        rating=review.rating,
        body=review.body,
        reply_body=review.reply_body,
        replied_at=review.replied_at,
        is_hidden=review.is_hidden,
        reported=review.reported_at is not None,
        report_reason=review.report_reason if staff else None,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


async def rating_summaries(
    db: AsyncSession, app_ids: list[UUID]
) -> dict[UUID, tuple[float, int]]:
    """``{app_id: (average, count)}`` over visible reviews."""
    if not app_ids:
        return {}
    rows = await db.execute(
        select(
            AppReviewModel.app_id,
            func.avg(AppReviewModel.rating),
            func.count(AppReviewModel.id),
        )
        .where(AppReviewModel.app_id.in_(app_ids), AppReviewModel.is_hidden.is_(False))
        .group_by(AppReviewModel.app_id)
    )
    return {a: (round(float(avg), 2), n) for a, avg, n in rows.all()}


async def _summary(db: AsyncSession, app_filter) -> RatingSummary:
    rows = (
        await db.execute(
            select(AppReviewModel.rating, func.count(AppReviewModel.id))
            .where(app_filter, AppReviewModel.is_hidden.is_(False))
            .group_by(AppReviewModel.rating)
        )
    ).all()
    dist = dict.fromkeys(range(1, 6), 0) | dict(rows)
    count = sum(dist.values())
    return RatingSummary(
        average=round(sum(r * n for r, n in dist.items()) / count, 2)
        if count
        else None,
        count=count,
        distribution=dist,
    )


async def _page(
    db: AsyncSession, where: list, *, rating, page, page_size, staff=False
) -> tuple[list[ReviewOut], int]:
    q = (
        select(AppReviewModel, StoreModel.name, AppModel.name)
        .join(AppModel, AppModel.id == AppReviewModel.app_id)
        .outerjoin(StoreModel, StoreModel.id == AppReviewModel.store_id)
        .where(*where)
    )
    if rating:
        q = q.where(AppReviewModel.rating == rating)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = (
        await db.execute(
            q.order_by(AppReviewModel.created_at.desc(), AppReviewModel.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return [_out(r, s, a, staff=staff) for r, s, a in rows], total or 0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def can_review(db: AsyncSession, app_id: UUID, store_id: UUID) -> bool:
    """Installed for at least 7 days, now or in a past install."""
    now = datetime.now(UTC)
    installed = await db.scalar(
        select(AppInstallationModel.created_at).where(
            AppInstallationModel.app_id == app_id,
            AppInstallationModel.store_id == store_id,
        )
    )
    if installed is not None and now - _aware(installed) >= MIN_INSTALLED:
        return True
    past = await db.execute(
        select(
            AppUninstallEventModel.installed_at, AppUninstallEventModel.created_at
        ).where(
            AppUninstallEventModel.app_id == app_id,
            AppUninstallEventModel.store_id == store_id,
            AppUninstallEventModel.installed_at.is_not(None),
        )
    )
    return any(_aware(end) - _aware(start) >= MIN_INSTALLED for start, end in past)


async def _app(db: AsyncSession, slug: str) -> AppModel:
    app = await db.scalar(select(AppModel).where(AppModel.slug == slug))
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    return app


async def _mine(db: AsyncSession, app_id: UUID, store_id: UUID):
    return await db.scalar(
        select(AppReviewModel).where(
            AppReviewModel.app_id == app_id, AppReviewModel.store_id == store_id
        )
    )


def _reported(db: AsyncSession, review: AppReviewModel, reason: str, app_name: str):
    review.reported_at = datetime.now(UTC)
    review.report_reason = reason.strip()
    admin_notifications.notify(
        db,
        title="App review reported",
        body=app_name,
        url="/app-reviews",
        tag="app_review_reported",
    )


# ─── Merchant ─────────────────────────────────────────────────────


@merchant_router.get(
    "", response_model=SuccessResponse[ReviewPage], operation_id="list_app_reviews"
)
async def merchant_list(
    slug: str,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    rating: Annotated[int | None, Query(ge=1, le=5)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    app = await _app(db, slug)
    of_app = AppReviewModel.app_id == app.id
    items, total = await _page(
        db,
        [of_app, AppReviewModel.is_hidden.is_(False)],
        rating=rating,
        page=page,
        page_size=page_size,
    )
    mine = await _mine(db, app.id, store.id)
    return SuccessResponse(
        data=ReviewPage(
            summary=await _summary(db, of_app),
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            mine=_out(mine, store.name) if mine else None,
            can_review=mine is not None or await can_review(db, app.id, store.id),
        )
    )


@merchant_router.put(
    "", response_model=SuccessResponse[ReviewOut], operation_id="upsert_app_review"
)
async def merchant_upsert(
    slug: str,
    body: ReviewIn,
    store: Annotated[Store, Depends(verify_store_ownership)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    email_service: Annotated[object, Depends(get_email_service)],
):
    """Write or edit this store's review."""
    app = await _app(db, slug)
    review = await _mine(db, app.id, store.id)
    if review is None and not await can_review(db, app.id, store.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Use the app for at least 7 days before reviewing it.",
        )
    await write_budget(user_id, "app_review_write", WRITES_PER_HOUR)
    text = (body.body or "").strip() or None
    created = review is None
    if created:
        review = AppReviewModel(
            app_id=app.id, store_id=store.id, is_hidden=False, user_id=user_id
        )
        db.add(review)
    review.rating = body.rating
    review.body = text
    await db.flush()
    await db.refresh(review)
    if created:
        await send_bilingual(
            email_service,
            await partner_email(db, app.developer_id),
            subject=(
                f"New {body.rating}-star review for {app.name}",
                f"تقييم جديد ({body.rating}/5) لتطبيق {app.name}",
            ),
            lines=(
                f"{store.name} reviewed {app.name}. You can reply publicly.",
                f"قيّم {store.name} تطبيق {app.name}. يمكنك الرد علنًا.",
            ),
            url=partners_url("/reviews"),
            quote=text,
        )
        logger.info("app_review_created", app=app.slug, rating=body.rating)
    return SuccessResponse(data=_out(review, store.name))


@merchant_router.delete(
    "", response_model=SuccessResponse[dict], operation_id="delete_app_review"
)
async def merchant_delete(
    slug: str,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    app = await _app(db, slug)
    review = await _mine(db, app.id, store.id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    await db.delete(review)
    await db.flush()
    return SuccessResponse(data={"id": str(review.id)}, message="Review deleted")


@merchant_router.post(
    "/{review_id}/report",
    response_model=SuccessResponse[dict],
    operation_id="report_app_review",
)
async def merchant_report(
    slug: str,
    review_id: UUID,
    body: ReportIn,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    app = await _app(db, slug)
    review = await db.get(AppReviewModel, review_id)
    if review is None or review.app_id != app.id or review.is_hidden:
        raise HTTPException(status_code=404, detail="Review not found")
    await write_budget(user_id, "app_review_write", WRITES_PER_HOUR)
    _reported(db, review, body.reason, app.name)
    await db.flush()
    return SuccessResponse(data={"id": str(review.id)}, message="Reported")


# ─── Partner ──────────────────────────────────────────────────────


async def _owned(db: AsyncSession, owner_id: UUID, review_id: UUID):
    row = (
        await db.execute(
            select(AppReviewModel, AppModel)
            .join(AppModel, AppModel.id == AppReviewModel.app_id)
            .where(AppReviewModel.id == review_id, AppModel.developer_id == owner_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return row


@partner_router.get(
    "",
    response_model=SuccessResponse[ReviewPage],
    operation_id="list_partner_reviews",
)
async def partner_list(
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    app_id: UUID | None = None,
    rating: Annotated[int | None, Query(ge=1, le=5)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
):
    apps = select(AppModel.id).where(AppModel.developer_id == owner_id)
    if app_id:
        apps = apps.where(AppModel.id == app_id)
    of_apps = AppReviewModel.app_id.in_(apps)
    items, total = await _page(
        db, [of_apps], rating=rating, page=page, page_size=page_size
    )
    return SuccessResponse(
        data=ReviewPage(
            summary=await _summary(db, of_apps),
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@partner_router.put(
    "/{review_id}/reply",
    response_model=SuccessResponse[ReviewOut],
    operation_id="reply_partner_review",
)
async def partner_reply(
    review_id: UUID,
    body: ReplyIn,
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """The partner's one public reply; sending again edits it."""
    review, app = await _owned(db, owner_id, review_id)
    await write_budget(user_id, "app_review_write", WRITES_PER_HOUR)
    review.reply_body = body.body.strip()
    review.replied_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(review)
    store_name = await db.scalar(
        select(StoreModel.name).where(StoreModel.id == review.store_id)
    )
    return SuccessResponse(data=_out(review, store_name, app.name))


@partner_router.post(
    "/{review_id}/report",
    response_model=SuccessResponse[dict],
    operation_id="report_partner_review",
)
async def partner_report(
    review_id: UUID,
    body: ReportIn,
    owner_id: Annotated[UUID, Depends(require_approved_partner)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    review, app = await _owned(db, owner_id, review_id)
    await write_budget(user_id, "app_review_write", WRITES_PER_HOUR)
    _reported(db, review, body.reason, app.name)
    await db.flush()
    return SuccessResponse(data={"id": str(review.id)}, message="Reported")


# ─── Admin ────────────────────────────────────────────────────────


@admin_router.get("", response_model=SuccessResponse[ReviewPage])
async def admin_list(
    _: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    queue: Literal["reported", "hidden", "all"] = "reported",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
):
    where = {
        "reported": [AppReviewModel.reported_at.is_not(None)],
        "hidden": [AppReviewModel.is_hidden.is_(True)],
        "all": [],
    }[queue]
    items, total = await _page(
        db, where, rating=None, page=page, page_size=page_size, staff=True
    )
    return SuccessResponse(
        data=ReviewPage(
            summary=RatingSummary(average=None, count=total, distribution={}),
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@admin_router.post("/{review_id}/moderate", response_model=SuccessResponse[ReviewOut])
async def admin_moderate(
    review_id: UUID,
    body: ModerateIn,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """``hide`` / ``unhide`` set visibility; each also clears the report, as
    does ``dismiss`` (the report was unfounded)."""
    review = await db.get(AppReviewModel, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    if body.action != "dismiss":
        review.is_hidden = body.action == "hide"
    review.reported_at = None
    review.report_reason = None
    await db.flush()
    await db.refresh(review)
    logger.info(
        "app_review_moderated",
        review_id=str(review.id),
        action=body.action,
        admin_id=str(admin_id),
    )
    store_name = await db.scalar(
        select(StoreModel.name).where(StoreModel.id == review.store_id)
    )
    return SuccessResponse(data=_out(review, store_name, staff=True))

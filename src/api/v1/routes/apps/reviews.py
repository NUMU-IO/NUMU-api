"""``/api/v1/apps/reviews/…``: the merchant screens of «آراء العملاء».

Called by the app's front inside the hub with the hub's session token; the
store always comes from the token (``AppSession.store_id``), never from the
request. The reviews are core ``product_reviews`` rows: publishing flips
``is_approved``, so every theme, Loyalty and the JSON-LD see the same set.
Nothing here returns a shopper's phone or email.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.app_session import (
    AppSession,
    app_router,
    require_app_session,
)
from src.api.dependencies.database import get_db
from src.application.services import reviews_service as svc
from src.application.services.back_in_stock_whatsapp import can_send, whatsapp_state
from src.infrastructure.cache.product_cache import get_product_cache
from src.infrastructure.database.models.tenant.product_review import (
    ProductReviewModel as Review,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories import reviews_repository as repo

router = app_router(svc.SLUG)

Session = Annotated[AppSession, Depends(require_app_session(svc.SLUG))]
Db = Annotated[AsyncSession, Depends(get_db)]

TEMPLATE = "app_reviews_request_v1"


def _iso(value: Any) -> str | None:
    value = svc.aware(value)
    return value.isoformat() if value else None


def _review_out(r: Review, name: str, images: list | None, number: str | None) -> dict:
    return {
        "id": str(r.id),
        "tab": svc.tab_of(r.is_approved, r.rejected_reason),
        "rejected_reason": r.rejected_reason,
        "product": {
            "id": str(r.product_id),
            "name": name,
            "image": (images or [None])[0],
        },
        "order": {"id": str(r.order_id), "number": number} if r.order_id else None,
        "rating": r.rating,
        "title": r.title,
        "body": r.body,
        "name": r.reviewer_name,
        "verified": r.verified,
        "photos": [
            {"url": m.get("url"), "thumb_url": m.get("thumb_url")}
            for m in r.media or []
        ],
        "reply": {"body": r.reply_body, "at": _iso(r.replied_at)}
        if r.reply_body
        else None,
        "created_at": _iso(r.created_at),
    }


async def _bust(store_id: UUID) -> None:
    """Shoppers see the change: once per request, never once per review."""
    try:
        await get_product_cache().invalidate_store_products(store_id)
    except Exception:  # noqa: BLE001 — the change is saved; the cache expires anyway
        pass


@router.get("/home")
async def home(session: Session, db: Db) -> dict:
    store = await db.get(StoreModel, session.store_id)
    await repo.ensure_settings(db, store)
    state = await whatsapp_state(db, store, TEMPLATE)
    rows, _total = await repo.inbox(db, store.id, tab="pending", size=5)
    return {
        "whatsapp": {
            "mode": state["mode"],
            "transport": state["transport"],
            "access": state["access"],
            "access_reason": state["access_reason"],
            "usable": can_send(state),
            "template": {lang: state["template"].get(lang) for lang in ("ar", "en")},
        },
        "counts": await repo.tab_counts(db, store.id),
        "kpis": await repo.kpis(db, store.id, datetime.now(UTC)),
        "pending": [_review_out(*row) for row in rows],
    }


@router.get("/reviews")
async def reviews(
    session: Session,
    db: Db,
    tab: Literal["pending", "published", "rejected", "spam"] = "pending",
    rating: Annotated[int | None, Query(ge=1, le=5)] = None,
    verified: bool | None = None,
    photos: bool | None = None,
    product_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    rows, total = await repo.inbox(
        db,
        session.store_id,
        tab=tab,
        rating=rating,
        verified=verified,
        photos=photos,
        product_id=product_id,
        page=page,
    )
    return {
        "items": [_review_out(*row) for row in rows],
        "total": total,
        "page": page,
        "counts": await repo.tab_counts(db, session.store_id),
    }


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["publish", "reject", "spam"]
    #: Required to reject: spam, abuse, personal data, off topic, duplicate.
    #: Never "negative": a rule by star rating does not exist here.
    reason: Literal["abuse", "personal", "off_topic", "duplicate"] | None = None


def _apply(r: Review, body: ActionIn) -> None:
    if body.action == "publish":
        r.is_approved, r.rejected_reason = True, None
    elif body.action == "spam":
        r.is_approved, r.rejected_reason = False, "spam"
    else:
        if body.reason is None:
            raise HTTPException(status_code=422, detail="A reason is required.")
        r.is_approved, r.rejected_reason = False, body.reason
    r.updated_at = datetime.now(UTC)


async def _own(db: AsyncSession, session: AppSession, review_id: UUID) -> Review:
    r = await repo.review(db, session.store_id, review_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    return r


@router.patch("/reviews/{review_id}")
async def act(session: Session, db: Db, review_id: UUID, body: ActionIn) -> dict:
    r = await _own(db, session, review_id)
    _apply(r, body)
    await db.flush()
    await _bust(session.store_id)
    return {"id": str(r.id), "tab": svc.tab_of(r.is_approved, r.rejected_reason)}


class BulkIn(ActionIn):
    ids: list[UUID] = Field(min_length=1, max_length=100)


@router.post("/reviews/bulk")
async def bulk(session: Session, db: Db, body: BulkIn) -> dict:
    done = 0
    for review_id in body.ids:
        r = await repo.review(db, session.store_id, review_id)
        if r is not None:
            _apply(r, body)
            done += 1
    await db.flush()
    await _bust(session.store_id)
    return {"updated": done}


@router.delete("/reviews/{review_id}", status_code=204)
async def remove(session: Session, db: Db, review_id: UUID) -> None:
    r = await _own(db, session, review_id)
    keys = [k for m in r.media or [] for k in (m.get("key"), m.get("thumb_key")) if k]
    await db.execute(delete(Review).where(Review.id == r.id))
    await db.flush()
    await _bust(session.store_id)
    try:
        from src.api.dependencies.services import get_storage_service

        storage = get_storage_service()
        for key in keys:
            await storage.delete_file(key)
    except Exception:  # noqa: BLE001 — the review is gone; a stray photo is not shown
        pass


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Empty removes the reply.
    body: str = Field(max_length=2000)


@router.put("/reviews/{review_id}/reply")
async def reply(session: Session, db: Db, review_id: UUID, body: ReplyIn) -> dict:
    r = await _own(db, session, review_id)
    text = body.body.strip()
    r.reply_body, r.replied_at = (text, datetime.now(UTC)) if text else (None, None)
    r.updated_at = datetime.now(UTC)
    await db.flush()
    if r.is_approved:
        await _bust(session.store_id)
    return {"reply": {"body": r.reply_body, "at": _iso(r.replied_at)} if text else None}


@router.get("/requests")
async def requests(
    session: Session, db: Db, page: Annotated[int, Query(ge=1)] = 1
) -> dict:
    store = await db.get(StoreModel, session.store_id)
    rows, total = await repo.requests_page(db, store.id, page=page)
    return {
        "items": [
            {
                "order": {"id": str(r.order_id), "number": number},
                "status": r.status,
                "skip_reason": r.skip_reason,
                "due_at": _iso(r.due_at),
                "sent_at": _iso(r.sent_at),
                "opened_at": _iso(r.opened_at),
                "completed_at": _iso(r.completed_at),
            }
            for r, number in rows
        ],
        "total": total,
        "page": page,
        "sent_today": await repo.sent_today(db, store, datetime.now(UTC)),
        "daily_cap": (await repo.settings(db, store.id))["daily_cap"],
    }


async def _settings_out(db: AsyncSession, store: StoreModel) -> dict[str, Any]:
    row = await repo.ensure_settings(db, store)
    return svc.merged_settings(row) | svc.moderation(store.settings)


@router.get("/settings")
async def get_settings(session: Session, db: Db) -> dict:
    return await _settings_out(db, await db.get(StoreModel, session.store_id))


class SettingsIn(BaseModel):
    """Any subset. The publishing rule is the store's own review moderation,
    the same one core applies to every review."""

    model_config = ConfigDict(extra="forbid")

    requests_enabled: bool | None = None
    delay_days: int | None = Field(default=None, ge=svc.DELAY_MIN, le=svc.DELAY_MAX)
    daily_cap: int | None = Field(default=None, ge=0, le=svc.CAP_MAX)
    photos_enabled: bool | None = None
    moderation_mode: Literal["auto", "manual", "auto_with_filter"] | None = None
    flagged_words: list[Annotated[str, Field(max_length=60)]] | None = Field(
        default=None, max_length=200
    )


@router.put("/settings")
async def put_settings(session: Session, db: Db, body: SettingsIn) -> dict:
    store = await db.get(StoreModel, session.store_id)
    row = await repo.ensure_settings(db, store)
    changes = body.model_dump(exclude_none=True)
    current = svc.moderation(store.settings)
    mode = changes.pop("moderation_mode", current["moderation_mode"])
    words = changes.pop("flagged_words", current["flagged_words"])
    if body.moderation_mode is not None or body.flagged_words is not None:
        clean = list(dict.fromkeys(w.strip() for w in words if w.strip()))
        # A new dict, so SQLAlchemy sees the JSONB change.
        store.settings = {
            **(store.settings or {}),
            "review_moderation": {"mode": mode, "flagged_words": clean},
        }
    for key, value in changes.items():
        setattr(row, key, value)
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return await _settings_out(db, store)

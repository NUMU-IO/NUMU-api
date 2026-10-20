"""Reviews app «آراء العملاء»: the shopper routes.

``/storefront/store/{store_id}/apps/reviews/…`` is called by the store's own
host: the ``/reviews/write`` page and its form post, and the review section
through the relay (``/api/apps/reviews/…``). Both add the internal-service
headers, so the per-shopper rate limits apply.

- ``GET /write?t=`` and ``POST /write``: the buyer's link. The token is the
  only key, it opens one delivered order of this store for 60 days after the
  message, and each product of the order takes one review. Photos come inside
  the multipart post; each is checked from its bytes, stripped of EXIF and
  re-encoded before it is stored.
- ``GET /products/{product_id}``: the published reviews of one product.

A store without the app answers 404 "not installed" everywhere. This module
also registers the ``numueg.app/a/reviews/<subdomain>/<token>`` link.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from src.api.dependencies.database import get_db
from src.api.v1.routes.app_links import APP_LINKS
from src.application.services import reviews_service as svc
from src.application.services.app_install_gate import is_live
from src.application.services.back_in_stock import store_host
from src.application.services.review_moderation import (
    decide_review_visibility,
    public_reviewer_name,
)
from src.core.interfaces.services.storage_service import StorageBucket
from src.infrastructure.cache.product_cache import get_product_cache
from src.infrastructure.database.connection import commit_and_restore_rls
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.product_review import (
    ProductReviewModel as Review,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.external_services.image.image_processor import (
    ImageProcessingError,
    ImageProcessor,
)
from src.infrastructure.repositories import reviews_repository as repo

router = APIRouter(prefix=f"/apps/{svc.SLUG}", tags=["Storefront - Reviews"])

StoreId = Annotated[UUID, Path(description="Store ID")]
Db = Annotated[AsyncSession, Depends(get_db)]

NO_STORE = {"Cache-Control": "private, no-store"}
PAGE_SIZE = 10


def _iso(value: Any) -> str | None:
    value = svc.aware(value)
    return value.isoformat() if value else None


async def _store(db: AsyncSession, store_id: UUID) -> StoreModel:
    store = await db.get(StoreModel, store_id)
    if store is None or not await is_live(db, store_id, svc.SLUG):
        raise HTTPException(status_code=404, detail="not installed", headers=NO_STORE)
    return store


async def _link(
    db: AsyncSession, store: StoreModel, token: str
) -> tuple[str, Any, OrderModel | None]:
    """``(state, request, order)``; state is ``open``, ``expired`` or
    ``not_found``. Another store's token is not found here."""
    request = await repo.by_token(db, token) if token else None
    if request is None or request.store_id != store.id:
        return "not_found", None, None
    if not svc.link_open(request.sent_at, datetime.now(UTC)):
        return "expired", request, None
    order = await db.get(OrderModel, request.order_id)
    if order is None or not svc.reviewable(order):
        return "not_found", None, None
    return "open", request, order


@router.get("/write")
async def write_page(
    store_id: StoreId,
    db: Db,
    response: Response,
    t: Annotated[str, Query(max_length=64)] = "",
) -> dict:
    store = await _store(db, store_id)
    response.headers.update(NO_STORE)
    state, _request, order = await _link(db, store, t)
    out: dict[str, Any] = {"state": state, "store_name": store.name}
    if order is None:
        return out
    items = await repo.order_items(db, order)
    cfg = await repo.settings(db, store.id)
    return out | {
        "state": "done" if all(i["reviewed"] for i in items) else "open",
        "order_number": order.order_number,
        "first_name": (order.shipping_address or {}).get("first_name"),
        "items": items,
        "photos": cfg["photos_enabled"],
        "limits": {
            "title": svc.TITLE_MAX,
            "body": svc.BODY_MAX,
            "name": svc.NAME_MAX,
            "photos_per_review": svc.PHOTOS_PER_REVIEW,
            "photos_per_post": svc.PHOTOS_PER_POST,
            "photo_bytes": svc.PHOTO_BYTES,
        },
    }


def _bad(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message, headers=NO_STORE)


def _text(form: Any, key: str, limit: int) -> str | None:
    value = form.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad(f"{key} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise _bad(f"{key} is longer than {limit} characters.")
    return value or None


def _process(data: bytes) -> tuple[bytes, bytes]:
    """The photo and its thumbnail as WebP, EXIF gone. Raises
    ``ImageProcessingError`` for anything that is not an image."""
    p = ImageProcessor()
    img = p.normalize_mode(p.strip_exif(p.validate(data)))
    return (
        p.convert_to_webp(p.resize(img, svc.PHOTO_SIZE, svc.PHOTO_SIZE)),
        p.convert_to_webp(p.resize(img, svc.THUMB_SIZE, svc.THUMB_SIZE)),
    )


async def _store_photo(store_id: UUID, data: bytes) -> dict[str, str] | None:
    try:
        full, thumb = await asyncio.to_thread(_process, data)
    except ImageProcessingError:
        return None
    from src.api.dependencies.services import get_storage_service

    storage = get_storage_service()
    stem = f"{repo.media_prefix(store_id)}{secrets.token_hex(12)}"
    out: dict[str, str] = {}
    for name, content in (("", full), ("_thumb", thumb)):
        key = f"{stem}{name}.webp"
        uploaded = await storage.upload_file(
            file_content=content,
            filename=key,
            content_type="image/webp",
            bucket=StorageBucket.STORES,
            key=key,
        )
        out["thumb_key" if name else "key"] = uploaded.key
        out["thumb_url" if name else "url"] = uploaded.url
    return out


async def _drop_photos(media_of: list[list[dict[str, str]]]) -> None:
    """Best effort: a stray file is never shown, only stored."""
    from src.api.dependencies.services import get_storage_service

    storage = get_storage_service()
    for media in media_of:
        for item in media:
            for key in (item.get("key"), item.get("thumb_key")):
                try:
                    if key:
                        await storage.delete_file(key)
                except Exception:  # noqa: BLE001
                    pass


@router.post("/write")
async def write(
    store_id: StoreId, db: Db, request: Request, response: Response
) -> dict:
    """One review per product the buyer rated. The form: ``t``, ``name``, and
    for each product id ``P``: ``rating.P`` (1–5; unrated products are left
    for later), ``title.P``, ``body.P``, ``photos.P`` (files)."""
    store = await _store(db, store_id)
    response.headers.update(NO_STORE)
    form = await request.form()
    token = form.get("t")
    state, link, order = await _link(db, store, token if isinstance(token, str) else "")
    if order is None:
        raise HTTPException(status_code=404, detail=state, headers=NO_STORE)
    name = _text(form, "name", svc.NAME_MAX)
    cfg = await repo.settings(db, store.id)

    todo: list[tuple[UUID, int, str | None, str | None, list[UploadFile]]] = []
    for item in await repo.order_items(db, order):
        pid = item["product_id"]
        raw = form.get(f"rating.{pid}")
        if item["reviewed"] or raw is None or raw == "":
            continue
        if raw not in {"1", "2", "3", "4", "5"}:
            raise _bad(f"rating.{pid} must be 1 to 5.")
        photos = [
            f
            for f in form.getlist(f"photos.{pid}")
            if isinstance(f, UploadFile) and f.filename
        ]
        if len(photos) > svc.PHOTOS_PER_REVIEW:
            raise _bad(f"At most {svc.PHOTOS_PER_REVIEW} photos per product.")
        todo.append((
            UUID(pid),
            int(raw),
            _text(form, f"title.{pid}", svc.TITLE_MAX),
            _text(form, f"body.{pid}", svc.BODY_MAX),
            photos if cfg["photos_enabled"] else [],
        ))
    if not todo:
        raise _bad("Rate at least one product.")
    if sum(len(p) for *_rest, p in todo) > svc.PHOTOS_PER_POST:
        raise _bad(f"At most {svc.PHOTOS_PER_POST} photos at once.")

    shown = public_reviewer_name(
        name or (order.shipping_address or {}).get("first_name"),
        store.default_language,
    )
    # Decoding, re-encoding and uploading photos takes a while: end the
    # transaction first, or Postgres closes it once it sits idle for 60 s.
    await db.commit()
    media_of: list[list[dict[str, str]]] = []
    rejected_photos = created = published = 0
    try:
        for *_text_fields, photos in todo:
            media: list[dict[str, str]] = []
            media_of.append(media)  # first, so a failure midway knows its files
            for photo in photos:
                data = await photo.read(svc.PHOTO_BYTES + 1)
                stored = (
                    await _store_photo(store.id, data)
                    if len(data) <= svc.PHOTO_BYTES
                    else None
                )
                if stored is None:
                    rejected_photos += 1
                else:
                    media.append(stored)
        await commit_and_restore_rls(db)  # a fresh transaction, RLS context back

        for (product_id, rating, title, body, _photos), media in zip(
            todo, media_of, strict=True
        ):
            decision = decide_review_visibility(
                store_settings=store.settings, title=title, body=body
            )
            db.add(
                Review(
                    id=uuid4(),
                    tenant_id=store.tenant_id,
                    store_id=store.id,
                    product_id=product_id,
                    customer_id=order.customer_id,
                    order_id=order.id,
                    verified=True,
                    reviewer_name=shown,
                    rating=rating,
                    title=title,
                    body=body,
                    is_approved=decision.is_approved,
                    media=media or None,
                )
            )
            created += 1
            published += decision.is_approved
        now = datetime.now(UTC)
        link.status, link.updated_at = svc.COMPLETED, now
        link.completed_at = link.completed_at or now
        # Committed before the cache bust below, or a product page read in
        # between would cache the old rating again.
        await commit_and_restore_rls(db)
    except IntegrityError:
        # The same form sent twice: the first one saved the reviews. This
        # one's photos belong to nothing, so they go too.
        await db.rollback()
        await _drop_photos(media_of)
        raise HTTPException(status_code=409, detail="done", headers=NO_STORE) from None
    except Exception:
        # Nothing was saved, so the photos stored so far belong to nothing.
        await _drop_photos(media_of)
        raise
    if published:
        try:
            await get_product_cache().invalidate_store_products(store.id)
        except Exception:  # noqa: BLE001 — saved; the cache expires anyway
            pass
    return {
        "created": created,
        "published": published,
        "photos_rejected": rejected_photos,
    }


@router.get("/products/{product_id}")
async def product_reviews_first(
    store_id: StoreId, product_id: UUID, db: Db, response: Response
) -> dict:
    return await product_reviews(store_id, product_id, 1, db, response)


@router.get("/products/{product_id}/{page}")
async def product_reviews(
    store_id: StoreId,
    product_id: UUID,
    page: Annotated[int, Path(ge=1, le=500)],
    db: Db,
    response: Response,
) -> dict:
    """What ``lib-product-reviews`` shows: the summary and one page. The page
    is a path segment because the host's relay forwards no query string."""
    await _store(db, store_id)
    response.headers["Cache-Control"] = "public, max-age=60"
    rows = await repo.published_page(
        db, store_id, product_id, page=page, size=PAGE_SIZE
    )
    return {
        "summary": await repo.summary(db, store_id, product_id),
        "reviews": [
            {
                "id": str(r.id),
                "name": r.reviewer_name,
                "rating": r.rating,
                "title": r.title,
                "body": r.body,
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
            for r in rows[:PAGE_SIZE]
        ],
        "page": page,
        "has_more": len(rows) > PAGE_SIZE,
    }


async def resolve_link(db: AsyncSession, rest: str) -> str | None:
    """``<subdomain>/<token>`` → the store's write page. None (the apex site)
    for an unknown store; a bad token still lands on the page, which says so."""
    sub, _, token = rest.partition("/")
    store = (
        await db.execute(select(StoreModel).where(StoreModel.subdomain == sub))
    ).scalar_one_or_none()
    if store is None:
        return None
    request = await repo.by_token(db, token) if token else None
    if (
        request is not None
        and request.store_id == store.id
        and request.opened_at is None
    ):
        request.opened_at = datetime.now(UTC)
        await db.commit()
    return f"https://{store_host(store)}/reviews/write?t={quote(token, safe='')}"


APP_LINKS[svc.SLUG] = resolve_link

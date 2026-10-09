"""Back in Stock app: the shopper routes and the message links.

``/storefront/store/{store_id}/apps/back-in-stock/…`` is called by the store's
own host (``/api/apps/back-in-stock/…``), which adds the internal-service
headers, so the per-shopper storefront rate limit applies. Every route answers
"not installed" unless the app is live on the store.

The message links (``numueg.app/a/back-in-stock/<sub>/…``) are resolved here
too and registered in ``app_links.APP_LINKS``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.v1.routes.app_links import APP_LINKS
from src.application.services import back_in_stock as bis
from src.application.services.back_in_stock_whatsapp import whatsapp_usable
from src.infrastructure.database.models.tenant.back_in_stock import (
    BackInStockWaiterModel as Waiter,
)
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories import back_in_stock_repository as repo

router = APIRouter(prefix=f"/apps/{bis.SLUG}", tags=["Storefront - Back in Stock"])

StoreId = Annotated[UUID, Path(description="Store ID")]
Db = Annotated[AsyncSession, Depends(get_db)]


async def _live_store(db: AsyncSession, store_id: UUID) -> StoreModel:
    store = await db.get(StoreModel, store_id)
    if store is None or not await repo.is_live(db, store_id):
        raise HTTPException(status_code=404, detail="not installed")
    return store


def _error(code: int, key: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"code": key, "detail": message})


@router.get("/config")
async def config(store_id: StoreId, db: Db) -> dict:
    """The field the widget offers. While WhatsApp cannot send, a phone is
    never offered: ``phone_or_email`` becomes ``email``, and ``phone`` becomes
    ``None``, which hides the widget (it would only answer 409)."""
    store = await _live_store(db, store_id)
    contact = (await repo.settings(db, store_id))["contact"]
    if contact != "email" and not await whatsapp_usable(db, store):
        contact = "email" if contact == "phone_or_email" else None
    return {"contact": contact}


class SubscribeRequest(BaseModel):
    product_id: UUID
    variant_id: UUID | None = None
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    locale: Literal["ar", "en"] = "ar"
    #: Honeypot: hidden in the widget, filled only by bots.
    website: str | None = Field(default=None, max_length=200)


@router.post("/subscribe")
async def subscribe(store_id: StoreId, body: SubscribeRequest, db: Db):
    store = await _live_store(db, store_id)
    if body.website:
        # A bot: look like success, write nothing.
        return {"status": "subscribed"}

    cfg = await repo.settings(db, store_id)
    if body.phone and body.email:
        return _error(422, "one_contact", "Send a phone or an email, not both.")
    if body.phone:
        if cfg["contact"] == "email":
            return _error(422, "email_only", "This store takes email only.")
        contact, channel = bis.normalize_phone(body.phone), "whatsapp"
    elif body.email:
        if cfg["contact"] == "phone":
            return _error(422, "phone_only", "This store takes phone numbers only.")
        contact, channel = bis.normalize_email(body.email), "email"
    else:
        return _error(422, "contact_required", "A phone or an email is required.")
    if contact is None:
        return _error(
            422,
            f"invalid_{'phone' if channel == 'whatsapp' else 'email'}",
            "Invalid contact.",
        )

    product, variants = await repo.product_with_variants(db, store_id, body.product_id)
    variant = next((v for v in variants if v.id == body.variant_id), None)
    if product is None or (body.variant_id is not None and variant is None):
        raise HTTPException(status_code=404, detail="Product not found")
    if bis.is_buyable(product, variants, body.variant_id):
        return {"status": "available"}
    if channel == "whatsapp" and not await whatsapp_usable(db, store):
        return _error(
            409, "channel_unavailable", "WhatsApp alerts are not available here."
        )

    if await repo.existing_waiter(db, store_id, product.id, body.variant_id, contact):
        return {"status": "already"}
    now = datetime.now(UTC)
    if (
        await repo.waiting_for_contact(db, store_id, contact)
        >= bis.MAX_WAITING_PER_CONTACT
        or await repo.signups_today(db, store_id, now) >= cfg["signup_cap"]
    ):
        return _error(429, "busy", "Too many requests.")

    db.add(
        Waiter(
            store_id=store_id,
            tenant_id=store.tenant_id,
            product_id=product.id,
            variant_id=body.variant_id,
            channel=channel,
            contact=contact,
            locale=body.locale,
            status=bis.WAITING,
            product_title=product.name[:255],
            variant_title=repo.variant_title(variant),
            link_token=bis.new_token(),
            unsub_token=bis.new_token(),
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        # The same request twice at once: the unique waiting index kept one.
        await db.rollback()
        return {"status": "already"}
    return {"status": "subscribed"}


async def _unsubscribe_target(db: AsyncSession, store_id: UUID, token: str):
    found = await repo.by_token(db, Waiter.unsub_token, token)
    if found is None or found[1].id != store_id:
        raise HTTPException(status_code=404, detail="This link doesn't work.")
    return found


@router.get("/unsubscribe/{token}")
async def unsubscribe_info(store_id: StoreId, token: str, db: Db) -> dict:
    waiter, store = await _unsubscribe_target(db, store_id, token)
    return {"store_name": store.name, "contact": bis.mask(waiter.contact)}


@router.post("/unsubscribe/{token}")
async def unsubscribe(store_id: StoreId, token: str, db: Db) -> dict:
    waiter, store = await _unsubscribe_target(db, store_id, token)
    if waiter.contact:
        await repo.unsubscribe_contact(db, store_id, waiter.contact)
    return {
        "status": "unsubscribed",
        "store_name": store.name,
        "contact": bis.mask(waiter.contact),
    }


# ─── Message links: numueg.app/a/back-in-stock/<sub>/<token> | <sub>/u/<token> ──


async def resolve_link(db: AsyncSession, rest: str) -> str | None:
    """Where a message button goes. None (the apex site) for an unknown store."""
    sub, _, tail = rest.partition("/")
    store = (
        await db.execute(select(StoreModel).where(StoreModel.subdomain == sub))
    ).scalar_one_or_none()
    if store is None or not tail:
        return None
    home = f"https://{bis.store_host(store)}/"
    if tail.startswith("u/"):
        return bis.unsubscribe_url(store, tail[2:])

    found = await repo.by_token(db, Waiter.link_token, tail)
    if found is None or found[1].id != store.id:
        return home
    waiter = found[0]
    product = await db.get(ProductModel, waiter.product_id)
    if product is None:
        return home
    if waiter.clicked_at is None:
        waiter.clicked_at = datetime.now(UTC)
        await db.commit()
    return bis.product_url(store, product.slug, waiter.variant_id, waiter.channel)


APP_LINKS[bis.SLUG] = resolve_link

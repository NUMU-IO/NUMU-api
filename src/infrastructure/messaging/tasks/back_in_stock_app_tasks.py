"""Back in Stock app: restock checks, paced sends, attribution and retention.

Every task checks the install gate first, so a store that uninstalled (or
was never installed) does nothing. Core's own ``back_in_stock_tasks.py`` stays
until the app replaces it (plan Phase M).

The beat sweep (every 10 minutes) re-finds what an event missed: raw-SQL
stock writes publish no event, and prod Redis can evict queued messages.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update

from src.application.services import back_in_stock as bis
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.back_in_stock import (
    BackInStockWaiterModel as Waiter,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.repositories import back_in_stock_repository as repo
from src.infrastructure.tenancy.rls import enable_rls_bypass

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None

#: Orders in these states earned nothing.
_NO_REVENUE = {"cancelled", "refunded", "returned", "payment_failed", "draft"}


def _run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


# ─── restock_check: queue the waiters of one product, up to the caps ────


async def restock_check(store_id: UUID, product_id: UUID) -> dict:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        if not await repo.is_live(db, store_id):
            return {"skipped": "not_live"}
        product, variants = await repo.product_with_variants(db, store_id, product_id)
        store = await db.get(StoreModel, store_id)
        if product is None or store is None:
            return {"skipped": "no_product"}

        from src.application.services.back_in_stock_whatsapp import whatsapp_usable

        cfg = await repo.settings(db, store_id)
        now = datetime.now(UTC)
        room = {
            "whatsapp": (
                cfg["wa_cap"] - await repo.sent_today(db, store_id, "whatsapp", now)
            )
            if await whatsapp_usable(db, store)
            else 0,  # stay waiting until WhatsApp works again
            "email": cfg["email_cap"]
            - await repo.sent_today(db, store_id, "email", now),
        }
        pending = await repo.count(
            db, Waiter.store_id == store_id, Waiter.status == bis.QUEUED
        )

        waiting = (
            (
                await db.execute(
                    select(Waiter)
                    .where(
                        Waiter.store_id == store_id,
                        Waiter.product_id == product_id,
                        Waiter.status == bis.WAITING,
                    )
                    .order_by(Waiter.created_at)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )

        by_id = {v.id: v for v in variants}
        per_target: dict[Any, int] = {}
        queued: list[Waiter] = []
        for waiter in waiting:
            if not bis.is_buyable(product, variants, waiter.variant_id):
                continue
            variant = by_id.get(waiter.variant_id)
            tracked = variant is not None and variant.track_inventory
            units = variant.inventory_quantity if tracked else None
            target_room = bis.restock_cap(units) - per_target.get(waiter.variant_id, 0)
            if target_room <= 0 or room[waiter.channel] <= 0:
                continue
            waiter.status, waiter.queued_at, waiter.updated_at = bis.QUEUED, now, now
            per_target[waiter.variant_id] = per_target.get(waiter.variant_id, 0) + 1
            room[waiter.channel] -= 1
            queued.append(waiter)
        await db.commit()

    # ponytail: pacing counts the store's already-queued rows, not exact send
    # times; exact per-store slots need a Redis counter if bursts overlap.
    for waiter, countdown in zip(
        queued, bis.send_countdowns(pending + len(queued))[pending:], strict=True
    ):
        send_task.apply_async(args=[str(waiter.id)], countdown=countdown)
    return {"queued": len(queued)}


@celery_app.task(
    name="apps.back_in_stock.restock_check",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def restock_check_task(self, store_id: str, product_id: str) -> dict:
    try:
        return _run_async(restock_check(UUID(store_id), UUID(product_id)))
    except Exception as exc:  # noqa: BLE001 — the sweep finds it again anyway
        raise self.retry(exc=exc) from exc


# ─── send: one alert ────────────────────────────────────────────────────


async def send_email_alert(
    db: Any, store: Any, waiter: Any, product: Any
) -> tuple[str | None, str | None]:
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    variant = None
    if waiter.variant_id:
        from src.infrastructure.database.models.tenant.variant import VariantModel

        variant = await db.get(VariantModel, waiter.variant_id)
    priced = variant or product
    message = bis.email_message(
        store=store,
        waiter=waiter,
        product_slug=product.slug,
        image=getattr(variant, "image_url", None) or (product.images or [None])[0],
        price=bis.price_text(priced.price_amount, priced.price_currency, waiter.locale),
    )
    sent = await ResendEmailService().send_email(
        EmailMessage(
            to=waiter.contact,
            subject=message["subject"],
            html_content=message["html"],
            text_content=message["text"],
            from_name=store.name,
            headers=message["headers"],
        )
    )
    return ("email", None) if sent else (None, "email_failed")


async def send(waiter_id: UUID, *, final_try: bool = True) -> dict:
    """Send one queued alert. A transient failure raises (Celery retries)
    until ``final_try``; then the waiter is marked failed."""
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        waiter = await db.get(Waiter, waiter_id)
        if waiter is None or waiter.status != bis.QUEUED:
            return {"skipped": "not_queued"}
        store = await db.get(StoreModel, waiter.store_id)
        product, variants = await repo.product_with_variants(
            db, waiter.store_id, waiter.product_id
        )
        now = datetime.now(UTC)
        if not await repo.is_live(db, waiter.store_id) or product is None:
            waiter.status, waiter.updated_at = bis.WAITING, now
            await db.commit()
            return {"skipped": "not_live"}
        if not bis.is_buyable(product, variants, waiter.variant_id):
            # Sold out again before its turn: back in line for the next restock.
            waiter.status, waiter.queued_at, waiter.updated_at = bis.WAITING, None, now
            await db.commit()
            return {"skipped": "sold_out_again"}

        if waiter.channel == "whatsapp":
            from src.application.services.back_in_stock_whatsapp import send_alert

            message_id, reason = await send_alert(db, store, waiter)
            transient = reason is not None and reason not in _GUARD_REASONS
        else:
            try:
                message_id, reason = await send_email_alert(db, store, waiter, product)
            except Exception:  # noqa: BLE001 — provider error: retried below
                message_id, reason = None, "email_failed"
            transient = reason is not None
        if transient and not final_try:
            raise RuntimeError(f"back_in_stock_send_failed:{reason}")

        waiter.updated_at = datetime.now(UTC)
        if message_id:
            waiter.status, waiter.message_id, waiter.notified_at = (
                bis.NOTIFIED,
                message_id,
                waiter.updated_at,
            )
        else:
            waiter.status, waiter.fail_reason = (
                bis.FAILED,
                (reason or "send_failed")[:64],
            )
        await db.commit()
        return {"status": waiter.status}


#: Guard refusals: retrying cannot change them.
_GUARD_REASONS = {
    "opt_out",
    "no_opt_in",
    "invalid_phone",
    "no_phone",
    "template_not_approved",
    "whatsapp_not_connected",
    "merchant_setting_off",
}


@celery_app.task(
    name="apps.back_in_stock.send", bind=True, max_retries=2, default_retry_delay=120
)
def send_task(self, waiter_id: str) -> dict:
    try:
        return _run_async(
            send(UUID(waiter_id), final_try=self.request.retries >= self.max_retries)
        )
    except RuntimeError as exc:
        raise self.retry(exc=exc) from exc


# ─── attribution: did an alert bring this order ─────────────────────────


async def attribution(order_id: UUID) -> dict:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        order = await db.get(OrderModel, order_id)
        if (
            order is None
            or str(getattr(order.status, "value", order.status)) in _NO_REVENUE
        ):
            return {"matched": 0}
        if not await repo.is_live(db, order.store_id):
            return {"matched": 0}
        customer = (
            await db.get(CustomerModel, order.customer_id)
            if order.customer_id
            else None
        )
        contacts = {
            bis.normalize_phone(getattr(customer, "phone", None)),
            bis.normalize_phone((order.shipping_address or {}).get("phone")),
            bis.normalize_email(getattr(customer, "email", None)),
        } - {None}
        if not contacts:
            return {"matched": 0}
        ordered_at = order.created_at
        waiters = (
            (
                await db.execute(
                    select(Waiter).where(
                        Waiter.store_id == order.store_id,
                        Waiter.status == bis.NOTIFIED,
                        Waiter.purchased_at.is_(None),
                        Waiter.contact.in_(contacts),
                        Waiter.notified_at >= ordered_at - bis.ATTRIBUTION_WINDOW,
                    )
                )
            )
            .scalars()
            .all()
        )
        lines = [
            bis.OrderLine(
                product_id=_uuid(line.get("product_id")),
                variant_id=_uuid(line.get("variant_id")),
                total=int(line.get("total_price") or 0),
            )
            for line in (order.line_items or [])
        ]
        matched = 0
        for waiter in waiters:
            revenue = bis.attributed_revenue(
                notified_at=waiter.notified_at,
                ordered_at=ordered_at,
                product_id=waiter.product_id,
                variant_id=waiter.variant_id,
                lines=lines,
            )
            if revenue is not None:
                waiter.order_id, waiter.purchased_at, waiter.revenue = (
                    order.id,
                    ordered_at,
                    revenue,
                )
                matched += 1
        await db.commit()
        return {"matched": matched}


@celery_app.task(
    name="apps.back_in_stock.attribution",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def attribution_task(self, order_id: str) -> dict:
    try:
        return _run_async(attribution(UUID(order_id)))
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc) from exc


# ─── retention (BIS-D6) and the sweep ──────────────────────────────────


async def retention(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        rows = (
            (
                await db.execute(
                    select(Waiter).where(
                        or_(
                            (Waiter.status == bis.WAITING)
                            & (Waiter.created_at <= now - bis.WAITING_TTL),
                            Waiter.contact.is_not(None)
                            & (Waiter.updated_at <= now - bis.ERASE_AFTER),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        closed = erased = 0
        for row in rows:
            status, erase = bis.retention_actions(
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
                has_contact=row.contact is not None,
                now=now,
            )
            if status:
                row.status, row.updated_at = status, now
                closed += 1
            if erase:
                row.contact = None
                erased += 1
        await db.commit()
        return {"closed": closed, "erased": erased}


@celery_app.task(name="apps.back_in_stock.retention")
def retention_task() -> dict:
    return _run_async(retention())


#: A queued row older than this lost its send task (a worker restart, or
#: Redis evicting the message): it goes back in line.
STUCK_AFTER = timedelta(hours=1)


async def sweep(now: datetime | None = None) -> dict:
    """Every (store, product) with waiters gets a restock check."""
    now = now or datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        await db.execute(
            update(Waiter)
            .where(Waiter.status == bis.QUEUED, Waiter.queued_at <= now - STUCK_AFTER)
            .values(status=bis.WAITING, queued_at=None, updated_at=now)
        )
        await db.commit()
        pairs = (
            await db.execute(
                select(Waiter.store_id, Waiter.product_id)
                .where(Waiter.status == bis.WAITING)
                .group_by(Waiter.store_id, Waiter.product_id)
            )
        ).all()
    for store_id, product_id in pairs:
        restock_check_task.apply_async(args=[str(store_id), str(product_id)])
    return {"checked": len(pairs)}


@celery_app.task(name="apps.back_in_stock.sweep")
def sweep_task() -> dict:
    return _run_async(sweep())


# ─── merchant actions ──────────────────────────────────────────────────


async def send_test(
    db: Any, store: Any, *, channel: str, contact: str, product: Any, locale: str
) -> tuple[str | None, str | None]:
    """The real message, to the merchant's own contact; nothing is stored."""
    from src.application.services.back_in_stock_whatsapp import send_alert

    waiter = SimpleNamespace(
        id="test",
        contact=contact,
        channel=channel,
        locale=locale,
        product_title=product.name,
        variant_title=None,
        variant_id=None,
        link_token=bis.new_token(),
        unsub_token=bis.new_token(),
    )
    if channel == "whatsapp":
        return await send_alert(db, store, waiter)
    return await send_email_alert(db, store, waiter, product)

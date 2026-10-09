"""Back in Stock app: the queries its routes, handler and tasks share."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services import back_in_stock as bis
from src.application.services.app_install_gate import live_installs
from src.infrastructure.database.models.public.app import AppModel
from src.infrastructure.database.models.tenant.back_in_stock import (
    BackInStockSettingsModel,
    BackInStockWaiterModel,
)
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.variant import VariantModel

Waiter = BackInStockWaiterModel


async def is_live(db: AsyncSession, store_id: UUID) -> bool:
    """The install gate: the app is live on this store (APP-STANDARD § 4.1)."""
    stmt = await live_installs(db, store_id)
    return (await db.execute(stmt.where(AppModel.slug == bis.SLUG))).first() is not None


async def settings_row(
    db: AsyncSession, store_id: UUID
) -> BackInStockSettingsModel | None:
    return await db.get(BackInStockSettingsModel, store_id)


async def settings(db: AsyncSession, store_id: UUID) -> dict[str, Any]:
    return bis.merged_settings(await settings_row(db, store_id))


async def product_with_variants(
    db: AsyncSession, store_id: UUID, product_id: UUID
) -> tuple[ProductModel | None, list[VariantModel]]:
    product = await db.get(ProductModel, product_id)
    if product is None or product.store_id != store_id:
        return None, []
    variants = (
        (
            await db.execute(
                select(VariantModel)
                .where(VariantModel.product_id == product_id)
                .order_by(VariantModel.position)
            )
        )
        .scalars()
        .all()
    )
    return product, list(variants)


def variant_title(variant: VariantModel | None) -> str | None:
    values = [
        str(v) for v in ((variant.option_values or {}) if variant else {}).values() if v
    ]
    return " / ".join(values) or None


def start_of_day(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def count(db: AsyncSession, *where: Any) -> int:
    return await db.scalar(select(func.count()).select_from(Waiter).where(*where)) or 0


async def waiting_for_contact(db: AsyncSession, store_id: UUID, contact: str) -> int:
    return await count(
        db,
        Waiter.store_id == store_id,
        Waiter.contact == contact,
        Waiter.status == bis.WAITING,
    )


async def signups_today(db: AsyncSession, store_id: UUID, now: datetime) -> int:
    return await count(
        db, Waiter.store_id == store_id, Waiter.created_at >= start_of_day(now)
    )


async def sent_today(
    db: AsyncSession, store_id: UUID, channel: str, now: datetime
) -> int:
    """Sends already counted against today's cap: notified or on their way."""
    return await count(
        db,
        Waiter.store_id == store_id,
        Waiter.channel == channel,
        Waiter.status.in_((bis.QUEUED, bis.NOTIFIED)),
        func.coalesce(Waiter.notified_at, Waiter.queued_at) >= start_of_day(now),
    )


async def existing_waiter(
    db: AsyncSession,
    store_id: UUID,
    product_id: UUID,
    variant_id: UUID | None,
    contact: str,
) -> Waiter | None:
    return (
        await db.execute(
            select(Waiter).where(
                Waiter.store_id == store_id,
                Waiter.product_id == product_id,
                Waiter.variant_id.is_(None)
                if variant_id is None
                else Waiter.variant_id == variant_id,
                Waiter.contact == contact,
                Waiter.status == bis.WAITING,
            )
        )
    ).scalar_one_or_none()


async def by_token(
    db: AsyncSession, column: Any, token: str
) -> tuple[Waiter, StoreModel] | None:
    row = (
        await db.execute(
            select(Waiter, StoreModel)
            .join(StoreModel, StoreModel.id == Waiter.store_id)
            .where(column == token)
        )
    ).one_or_none()
    return (row[0], row[1]) if row else None


async def unsubscribe_contact(db: AsyncSession, store_id: UUID, contact: str) -> int:
    """Every waiting row of this contact in this store; other stores keep theirs."""
    result = await db.execute(
        update(Waiter)
        .where(
            Waiter.store_id == store_id,
            Waiter.contact == contact,
            Waiter.status == bis.WAITING,
        )
        .values(status=bis.UNSUBSCRIBED, updated_at=datetime.now(UTC))
    )
    return result.rowcount or 0


async def close_product(db: AsyncSession, store_id: UUID, product_id: UUID) -> int:
    result = await db.execute(
        update(Waiter)
        .where(
            Waiter.store_id == store_id,
            Waiter.product_id == product_id,
            Waiter.status == bis.WAITING,
        )
        .values(status=bis.CLOSED, updated_at=datetime.now(UTC))
    )
    return result.rowcount or 0


async def has_waiting(db: AsyncSession, store_id: UUID, product_id: UUID) -> bool:
    return (
        await db.scalar(
            select(Waiter.id)
            .where(
                Waiter.store_id == store_id,
                Waiter.product_id == product_id,
                Waiter.status == bis.WAITING,
            )
            .limit(1)
        )
        is not None
    )


async def purge_store(db: AsyncSession, store_id: UUID) -> None:
    """Uninstall + 30 days: nothing of the app stays for this store."""
    await db.execute(delete(Waiter).where(Waiter.store_id == store_id))
    await db.execute(
        delete(BackInStockSettingsModel).where(
            BackInStockSettingsModel.store_id == store_id
        )
    )


# ─── Shopper data rights ───────────────────────────────────────────────


def shopper_contacts(customer: Any) -> set[str]:
    """The contacts a shopper's waiter rows can carry: phone and email."""
    phone = getattr(customer, "phone", None)
    email = getattr(customer, "email", None)
    return {
        bis.normalize_phone(str(phone) if phone else None),
        bis.normalize_email(str(email) if email else None),
    } - {None}


async def export_for(
    db: AsyncSession, store_id: UUID, contacts: set[str]
) -> list[dict]:
    if not contacts:
        return []
    rows = (
        (
            await db.execute(
                select(Waiter).where(
                    Waiter.store_id == store_id, Waiter.contact.in_(contacts)
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "product": bis.product_label(w.product_title, w.variant_title),
            "channel": w.channel,
            "contact": w.contact,
            "status": w.status,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "notified_at": w.notified_at.isoformat() if w.notified_at else None,
        }
        for w in rows
    ]


async def delete_for(db: AsyncSession, store_id: UUID, contacts: set[str]) -> int:
    if not contacts:
        return 0
    result = await db.execute(
        delete(Waiter).where(Waiter.store_id == store_id, Waiter.contact.in_(contacts))
    )
    return result.rowcount or 0

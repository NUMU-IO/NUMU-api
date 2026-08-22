"""Merchant notification feed repository.

Keyset pagination on ``(created_at, id)`` so the bell dropdown and the
Notifications page can "load more" without skipping rows that arrive
while the merchant scrolls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.merchant_notification import (
    MerchantNotificationModel,
)

CURSOR_SEP = "|"


def encode_cursor(row: MerchantNotificationModel) -> str:
    return f"{row.created_at.isoformat()}{CURSOR_SEP}{row.id}"


def decode_cursor(cursor: str) -> tuple[datetime, UUID] | None:
    try:
        ts, raw_id = cursor.split(CURSOR_SEP, 1)
        return datetime.fromisoformat(ts), UUID(raw_id)
    except (ValueError, AttributeError):
        return None


class MerchantNotificationRepository:
    """SQLAlchemy implementation — explicit tenant filter alongside RLS."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(MerchantNotificationModel.tenant_id == tid)
        return query

    async def dedupe_exists(self, store_id: UUID, dedupe_key: str) -> bool:
        query = select(MerchantNotificationModel.id).where(
            MerchantNotificationModel.store_id == store_id,
            MerchantNotificationModel.dedupe_key == dedupe_key,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def create(self, model: MerchantNotificationModel) -> bool:
        """Insert; returns False when ``dedupe_key`` already exists."""
        if model.dedupe_key and await self.dedupe_exists(
            model.store_id, model.dedupe_key
        ):
            return False
        self.session.add(model)
        await self.session.flush()
        return True

    async def list_for_store(
        self,
        store_id: UUID,
        *,
        category: str | None = None,
        important_only: bool = False,
        unread_only: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[MerchantNotificationModel], str | None]:
        """Newest first. Returns ``(rows, next_cursor)``."""
        query = select(MerchantNotificationModel).where(
            MerchantNotificationModel.store_id == store_id
        )
        if category:
            query = query.where(MerchantNotificationModel.category == category)
        if important_only:
            query = query.where(MerchantNotificationModel.is_important.is_(True))
        if unread_only:
            query = query.where(MerchantNotificationModel.read_at.is_(None))
        if cursor:
            decoded = decode_cursor(cursor)
            if decoded:
                ts, last_id = decoded
                query = query.where(
                    or_(
                        MerchantNotificationModel.created_at < ts,
                        and_(
                            MerchantNotificationModel.created_at == ts,
                            MerchantNotificationModel.id < last_id,
                        ),
                    )
                )
        query = query.order_by(
            MerchantNotificationModel.created_at.desc(),
            MerchantNotificationModel.id.desc(),
        ).limit(limit + 1)
        result = await self.session.execute(self._tenant_filter(query))
        rows = list(result.scalars().all())
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = encode_cursor(rows[-1])
        return rows, next_cursor

    async def unread_counts(self, store_id: UUID) -> dict:
        """``{total, important, by_category}`` over unread rows."""
        query = (
            select(
                MerchantNotificationModel.category,
                MerchantNotificationModel.is_important,
                func.count(MerchantNotificationModel.id),
            )
            .where(
                MerchantNotificationModel.store_id == store_id,
                MerchantNotificationModel.read_at.is_(None),
            )
            .group_by(
                MerchantNotificationModel.category,
                MerchantNotificationModel.is_important,
            )
        )
        result = await self.session.execute(self._tenant_filter(query))
        by_category: dict[str, int] = {}
        total = 0
        important = 0
        for category, is_important, n in result.all():
            by_category[category] = by_category.get(category, 0) + n
            total += n
            if is_important:
                important += n
        return {"total": total, "important": important, "by_category": by_category}

    async def mark_read(self, store_id: UUID, ids: list[UUID]) -> int:
        if not ids:
            return 0
        stmt = (
            update(MerchantNotificationModel)
            .where(
                MerchantNotificationModel.store_id == store_id,
                MerchantNotificationModel.id.in_(ids),
                MerchantNotificationModel.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
        )
        tid = get_tenant_id()
        if tid:
            stmt = stmt.where(MerchantNotificationModel.tenant_id == tid)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def mark_all_read(
        self, store_id: UUID, *, category: str | None = None
    ) -> int:
        stmt = (
            update(MerchantNotificationModel)
            .where(
                MerchantNotificationModel.store_id == store_id,
                MerchantNotificationModel.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
        )
        if category:
            stmt = stmt.where(MerchantNotificationModel.category == category)
        tid = get_tenant_id()
        if tid:
            stmt = stmt.where(MerchantNotificationModel.tenant_id == tid)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

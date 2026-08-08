"""Repository for GOWA numbered-reply correlation."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_gowa_pending_reply import (
    WhatsAppGowaPendingReplyModel,
)

__all__ = ["DEFAULT_REPLY_TTL", "WhatsAppGowaPendingReplyRepository"]

# How long a numbered prompt stays answerable.
#
# 24 hours matches the window a customer plausibly acts in for an order
# notification, and deliberately echoes WhatsApp's own service-window length so
# the behaviour is not surprising. Long enough for someone who replies the next
# morning; short enough that a stray "1" cannot confirm a settled order.
DEFAULT_REPLY_TTL = timedelta(hours=24)


class WhatsAppGowaPendingReplyRepository:
    """Records numbered prompts and resolves inbound digits back to payloads."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        phone: str,
        message_type: str,
        payloads: dict[str, str],
        ttl: timedelta = DEFAULT_REPLY_TTL,
    ) -> WhatsAppGowaPendingReplyModel:
        """Remember what each digit means for this recipient."""
        row = WhatsAppGowaPendingReplyModel(
            tenant_id=tenant_id,
            store_id=store_id,
            phone=phone,
            message_type=message_type,
            payloads=payloads,
            expires_at=datetime.now(UTC) + ttl,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def resolve(
        self, phone: str, digit: str
    ) -> tuple[WhatsAppGowaPendingReplyModel, str] | None:
        """Newest live prompt for ``phone`` that defines ``digit``.

        Walks candidates newest-first rather than taking only the single most
        recent row: a customer may have been sent a delivery check after a
        confirm request, and replying "3" when the newest prompt only offers
        1-2 should fall through to the prompt that does define 3, not silently
        do nothing.

        Returns the row and the payload, or None when nothing matches.
        """
        now = datetime.now(UTC)
        result = await self.session.execute(
            select(WhatsAppGowaPendingReplyModel)
            .where(
                WhatsAppGowaPendingReplyModel.phone == phone,
                WhatsAppGowaPendingReplyModel.expires_at > now,
                WhatsAppGowaPendingReplyModel.consumed_at.is_(None),
            )
            .order_by(WhatsAppGowaPendingReplyModel.created_at.desc())
            .limit(10)
        )
        for row in result.scalars():
            payload = (row.payloads or {}).get(digit)
            if payload:
                return row, str(payload)
        return None

    async def mark_consumed(self, reply_id: UUID) -> None:
        """Single-use: stop a repeated digit driving the action twice."""
        await self.session.execute(
            update(WhatsAppGowaPendingReplyModel)
            .where(WhatsAppGowaPendingReplyModel.id == reply_id)
            .values(consumed_at=datetime.now(UTC))
        )

    async def purge_expired(self, older_than: timedelta = timedelta(days=7)) -> int:
        """Drop rows well past expiry. Safe to run from a periodic task.

        Kept for a while after expiring so support can still answer "did we ask
        them, and what did we ask?" during the period a dispute is likely.
        """
        cutoff = datetime.now(UTC) - older_than
        result = await self.session.execute(
            delete(WhatsAppGowaPendingReplyModel).where(
                WhatsAppGowaPendingReplyModel.expires_at < cutoff
            )
        )
        return int(result.rowcount or 0)

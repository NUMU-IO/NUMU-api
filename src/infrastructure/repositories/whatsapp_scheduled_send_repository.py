"""Repository for the whatsapp_scheduled_sends queue.

Dispatcher (T061) uses ``list_due`` with FOR UPDATE SKIP LOCKED so two
concurrent workers cannot grab the same row (FR-014, FR-015).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_scheduled_send import (
    WhatsAppScheduledSendModel,
)


class WhatsAppScheduledSendRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Dispatcher path ───────────────────────────────────────────

    async def list_due(
        self,
        now: datetime,
        limit: int = 100,
    ) -> list[WhatsAppScheduledSendModel]:
        """Return up to ``limit`` pending rows whose ``scheduled_for`` is in
        the past, locked for update with SKIP LOCKED so peer workers do not
        grab the same rows.

        Caller MUST have ``app.current_tenant`` set; without it RLS returns
        an empty list — that's the intended behaviour for the dispatcher
        which iterates per-tenant.
        """
        stmt = (
            select(WhatsAppScheduledSendModel)
            .where(
                WhatsAppScheduledSendModel.status == "pending",
                WhatsAppScheduledSendModel.scheduled_for <= now,
            )
            .order_by(WhatsAppScheduledSendModel.scheduled_for)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    # ── Writes ────────────────────────────────────────────────────

    async def create(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        phone: str,
        scheduled_for: datetime,
        template_id: UUID | None = None,
        template_params: dict[str, Any] | None = None,
        text_message: str | None = None,
        customer_id: UUID | None = None,
        related_order_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> WhatsAppScheduledSendModel:
        if (template_id is None) == (text_message is None):
            raise ValueError(
                "Exactly one of template_id or text_message must be provided."
            )
        now = datetime.now(UTC)
        row = WhatsAppScheduledSendModel(
            tenant_id=tenant_id,
            store_id=store_id,
            customer_id=customer_id,
            phone=phone,
            template_id=template_id,
            template_params=template_params,
            text_message=text_message,
            scheduled_for=scheduled_for,
            status="pending",
            related_order_id=related_order_id,
            created_by=created_by,
            created_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def cancel(self, send_id: UUID) -> bool:
        """Cancel a single pending row by id. Returns True if a row moved to
        ``cancelled``; False if no pending row exists with that id.
        """
        result = await self.session.execute(
            update(WhatsAppScheduledSendModel)
            .where(
                WhatsAppScheduledSendModel.id == send_id,
                WhatsAppScheduledSendModel.status == "pending",
            )
            .values(status="cancelled")
        )
        await self.session.flush()
        return (result.rowcount or 0) > 0

    async def cancel_by_order(self, order_id: UUID) -> int:
        """Cascade-cancel all pending rows tied to an order (FR-016)."""
        result = await self.session.execute(
            update(WhatsAppScheduledSendModel)
            .where(
                WhatsAppScheduledSendModel.related_order_id == order_id,
                WhatsAppScheduledSendModel.status == "pending",
            )
            .values(status="cancelled")
        )
        await self.session.flush()
        return result.rowcount or 0

    async def mark_sent(
        self,
        send_id: UUID,
        sent_at: datetime | None = None,
    ) -> None:
        await self.session.execute(
            update(WhatsAppScheduledSendModel)
            .where(WhatsAppScheduledSendModel.id == send_id)
            .values(
                status="sent",
                dispatched_at=sent_at or datetime.now(UTC),
                sent_at=sent_at or datetime.now(UTC),
            )
        )
        await self.session.flush()

    async def mark_skipped(self, send_id: UUID, reason: str) -> None:
        """Guard rejected at dispatch-time — FR-017."""
        await self.session.execute(
            update(WhatsAppScheduledSendModel)
            .where(WhatsAppScheduledSendModel.id == send_id)
            .values(
                status="skipped",
                skip_reason=reason,
                dispatched_at=datetime.now(UTC),
            )
        )
        await self.session.flush()

    async def mark_failed(self, send_id: UUID, reason: str) -> None:
        await self.session.execute(
            update(WhatsAppScheduledSendModel)
            .where(WhatsAppScheduledSendModel.id == send_id)
            .values(
                status="failed",
                failure_reason=reason,
                dispatched_at=datetime.now(UTC),
            )
        )
        await self.session.flush()

    # ── Reads ─────────────────────────────────────────────────────

    async def get_by_id(self, send_id: UUID) -> WhatsAppScheduledSendModel | None:
        result = await self.session.execute(
            select(WhatsAppScheduledSendModel).where(
                WhatsAppScheduledSendModel.id == send_id
            )
        )
        return result.scalar_one_or_none()

    async def list_by_store(
        self,
        store_id: UUID,
        *,
        status: str | None = None,
        related_order_id: UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[WhatsAppScheduledSendModel], int]:
        query = select(WhatsAppScheduledSendModel).where(
            WhatsAppScheduledSendModel.store_id == store_id
        )
        count_query = select(func.count(WhatsAppScheduledSendModel.id)).where(
            WhatsAppScheduledSendModel.store_id == store_id
        )
        if status:
            query = query.where(WhatsAppScheduledSendModel.status == status)
            count_query = count_query.where(WhatsAppScheduledSendModel.status == status)
        if related_order_id:
            query = query.where(
                WhatsAppScheduledSendModel.related_order_id == related_order_id
            )
            count_query = count_query.where(
                WhatsAppScheduledSendModel.related_order_id == related_order_id
            )
        query = (
            query.order_by(desc(WhatsAppScheduledSendModel.scheduled_for))
            .offset(skip)
            .limit(limit)
        )
        rows = (await self.session.execute(query)).scalars().all()
        total = (await self.session.execute(count_query)).scalar() or 0
        return list(rows), total

    # ── GDPR cascade (TASK-SEC-001) ───────────────────────────────

    async def delete_by_customer(self, customer_id: UUID) -> int:
        result = await self.session.execute(
            delete(WhatsAppScheduledSendModel).where(
                WhatsAppScheduledSendModel.customer_id == customer_id
            )
        )
        await self.session.flush()
        return result.rowcount or 0

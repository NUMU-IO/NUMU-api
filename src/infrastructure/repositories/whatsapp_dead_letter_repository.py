"""Repository for whatsapp_dead_letters.

90-day retention (FR-035a) is enforced by the purge Celery task (T109)
calling ``purge_older_than(cutoff)``.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_dead_letter import (
    WhatsAppDeadLetterModel,
)


class WhatsAppDeadLetterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        phone: str,
        originating_context: str,
        error_classification: str,
        error_history: list[dict[str, Any]],
        customer_id: UUID | None = None,
        template_id: UUID | None = None,
        template_params: dict[str, Any] | None = None,
        text_message: str | None = None,
        originating_context_id: UUID | None = None,
        final_error_code: str | None = None,
    ) -> WhatsAppDeadLetterModel:
        now = datetime.now(UTC)
        row = WhatsAppDeadLetterModel(
            tenant_id=tenant_id,
            store_id=store_id,
            phone=phone,
            customer_id=customer_id,
            template_id=template_id,
            template_params=template_params,
            text_message=text_message,
            originating_context=originating_context,
            originating_context_id=originating_context_id,
            error_history=error_history,
            error_classification=error_classification,
            final_error_code=final_error_code,
            replay_state="not_replayed",
            created_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_by_id(self, dl_id: UUID) -> WhatsAppDeadLetterModel | None:
        result = await self.session.execute(
            select(WhatsAppDeadLetterModel).where(WhatsAppDeadLetterModel.id == dl_id)
        )
        return result.scalar_one_or_none()

    async def list_by_store(
        self,
        store_id: UUID,
        *,
        originating_context: str | None = None,
        replay_state: str | None = None,
        error_classification: str | None = None,
        created_after: datetime | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[WhatsAppDeadLetterModel], int]:
        query = select(WhatsAppDeadLetterModel).where(
            WhatsAppDeadLetterModel.store_id == store_id
        )
        count_query = select(func.count(WhatsAppDeadLetterModel.id)).where(
            WhatsAppDeadLetterModel.store_id == store_id
        )
        if originating_context:
            query = query.where(
                WhatsAppDeadLetterModel.originating_context == originating_context
            )
            count_query = count_query.where(
                WhatsAppDeadLetterModel.originating_context == originating_context
            )
        if replay_state:
            query = query.where(WhatsAppDeadLetterModel.replay_state == replay_state)
            count_query = count_query.where(
                WhatsAppDeadLetterModel.replay_state == replay_state
            )
        if error_classification:
            query = query.where(
                WhatsAppDeadLetterModel.error_classification == error_classification
            )
            count_query = count_query.where(
                WhatsAppDeadLetterModel.error_classification == error_classification
            )
        if created_after:
            query = query.where(WhatsAppDeadLetterModel.created_at >= created_after)
            count_query = count_query.where(
                WhatsAppDeadLetterModel.created_at >= created_after
            )
        query = (
            query.order_by(desc(WhatsAppDeadLetterModel.created_at))
            .offset(skip)
            .limit(limit)
        )
        rows = (await self.session.execute(query)).scalars().all()
        total = (await self.session.execute(count_query)).scalar() or 0
        return list(rows), total

    async def mark_replaying(self, dl_id: UUID) -> bool:
        """Transitions ``not_replayed`` → ``replaying``. Returns False if the
        row is in any other state (use case T106 uses this to refuse double-
        replay attempts).
        """
        result = await self.session.execute(
            update(WhatsAppDeadLetterModel)
            .where(
                WhatsAppDeadLetterModel.id == dl_id,
                WhatsAppDeadLetterModel.replay_state == "not_replayed",
            )
            .values(replay_state="replaying")
        )
        await self.session.flush()
        return (result.rowcount or 0) > 0

    async def mark_replayed(
        self,
        dl_id: UUID,
        *,
        success: bool,
        replayed_by: UUID | None,
        replayed_send_id: UUID | None,
    ) -> None:
        await self.session.execute(
            update(WhatsAppDeadLetterModel)
            .where(WhatsAppDeadLetterModel.id == dl_id)
            .values(
                replay_state="replayed_success" if success else "replayed_failed",
                replayed_at=datetime.now(UTC),
                replayed_by=replayed_by,
                replayed_send_id=replayed_send_id,
            )
        )
        await self.session.flush()

    # ── 90-day purge (FR-035a / T109) ─────────────────────────────

    async def purge_older_than(self, cutoff: datetime, batch_size: int = 1000) -> int:
        """Delete rows older than ``cutoff``. Batched to avoid long locks.
        Returns total purged count.

        The Celery beat task calls this daily; ``cutoff = NOW() - 90 days``.
        """
        total_deleted = 0
        while True:
            # Select a batch of ids first, then delete by id — keeps each
            # statement short and avoids holding the row lock across the
            # whole purge.
            id_rows = (
                (
                    await self.session.execute(
                        select(WhatsAppDeadLetterModel.id)
                        .where(WhatsAppDeadLetterModel.created_at < cutoff)
                        .limit(batch_size)
                    )
                )
                .scalars()
                .all()
            )
            if not id_rows:
                break
            result = await self.session.execute(
                delete(WhatsAppDeadLetterModel).where(
                    WhatsAppDeadLetterModel.id.in_(id_rows)
                )
            )
            await self.session.flush()
            total_deleted += result.rowcount or 0
        return total_deleted

    # ── GDPR cascade (TASK-SEC-001) ───────────────────────────────

    async def delete_by_customer(self, customer_id: UUID) -> int:
        result = await self.session.execute(
            delete(WhatsAppDeadLetterModel).where(
                WhatsAppDeadLetterModel.customer_id == customer_id
            )
        )
        await self.session.flush()
        return result.rowcount or 0

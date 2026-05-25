"""Repository for WhatsApp opt-in / opt-out tracking.

History-preserving: re-opting after an opt-out always creates a new row
(FR-012); existing rows are never mutated to reset ``opted_out_at``.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_opt_in import (
    WhatsAppOptInModel,
)


class WhatsAppOptInRepository:
    """CRUD + lookup helpers used by the send guard and storefront opt-in."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Send-guard lookups ─────────────────────────────────────────

    async def get_active(self, store_id: UUID, phone: str) -> WhatsAppOptInModel | None:
        """Return the most recent active opt-in row for (store, phone), or None."""
        result = await self.session.execute(
            select(WhatsAppOptInModel)
            .where(
                WhatsAppOptInModel.store_id == store_id,
                WhatsAppOptInModel.phone == phone,
                WhatsAppOptInModel.opted_out_at.is_(None),
            )
            .order_by(desc(WhatsAppOptInModel.opted_in_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def has_opt_out(self, store_id: UUID, phone: str) -> bool:
        """True if ANY revoked row exists for (store, phone) — explicit opt-out
        is honoured even if a later re-opt exists (latest-wins is decided at
        the active-row level; opt-out signals merchant should respect prior
        intent for marketing-class sends).

        Spec FR-011 + clarification Q1: opt-out always blocks, except for the
        STOP-ack bypass allowlist. The guard checks both ``get_active`` and
        ``has_opt_out`` separately and the bypass logic decides.
        """
        # We use "is there an active opt-in?" as the positive signal and a
        # separate query "is the LATEST row opted-out?" as the negative.
        result = await self.session.execute(
            select(WhatsAppOptInModel)
            .where(
                WhatsAppOptInModel.store_id == store_id,
                WhatsAppOptInModel.phone == phone,
            )
            .order_by(desc(WhatsAppOptInModel.opted_in_at))
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        return latest is not None and latest.opted_out_at is not None

    # ── Writes ────────────────────────────────────────────────────

    async def create(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        phone: str,
        source: str,
        customer_id: UUID | None = None,
    ) -> WhatsAppOptInModel:
        """Create a new opt-in row. Phone MUST already be canonicalized E.164."""
        now = datetime.now(UTC)
        row = WhatsAppOptInModel(
            tenant_id=tenant_id,
            store_id=store_id,
            customer_id=customer_id,
            phone=phone,
            source=source,
            opted_in_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def revoke_active(
        self,
        store_id: UUID,
        phone: str,
        reason: str,
    ) -> WhatsAppOptInModel | None:
        """Mark the active opt-in row (if any) as opted out. Idempotent: if no
        active row exists returns None.
        """
        row = await self.get_active(store_id, phone)
        if row is None:
            return None
        now = datetime.now(UTC)
        await self.session.execute(
            update(WhatsAppOptInModel)
            .where(WhatsAppOptInModel.id == row.id)
            .values(opted_out_at=now, opt_out_reason=reason)
        )
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def attach_customer(
        self,
        store_id: UUID,
        phone: str,
        customer_id: UUID,
    ) -> int:
        """Backfill customer_id on opt-in rows where it's still null (FR-007)."""
        result = await self.session.execute(
            update(WhatsAppOptInModel)
            .where(
                WhatsAppOptInModel.store_id == store_id,
                WhatsAppOptInModel.phone == phone,
                WhatsAppOptInModel.customer_id.is_(None),
            )
            .values(customer_id=customer_id)
        )
        await self.session.flush()
        return result.rowcount or 0

    # ── List / pagination ─────────────────────────────────────────

    async def list_by_store(
        self,
        store_id: UUID,
        *,
        phone: str | None = None,
        active_only: bool = False,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[WhatsAppOptInModel], int]:
        query = select(WhatsAppOptInModel).where(
            WhatsAppOptInModel.store_id == store_id
        )
        count_query = select(func.count(WhatsAppOptInModel.id)).where(
            WhatsAppOptInModel.store_id == store_id
        )
        if phone:
            query = query.where(WhatsAppOptInModel.phone == phone)
            count_query = count_query.where(WhatsAppOptInModel.phone == phone)
        if active_only:
            query = query.where(WhatsAppOptInModel.opted_out_at.is_(None))
            count_query = count_query.where(WhatsAppOptInModel.opted_out_at.is_(None))
        query = (
            query.order_by(desc(WhatsAppOptInModel.opted_in_at))
            .offset(skip)
            .limit(limit)
        )
        rows = (await self.session.execute(query)).scalars().all()
        total = (await self.session.execute(count_query)).scalar() or 0
        return list(rows), total

    # ── GDPR cascade (TASK-SEC-001) ───────────────────────────────

    async def delete_by_customer(self, customer_id: UUID) -> int:
        """Used by the customers/redact webhook handler to purge a customer's
        opt-in rows across all their stores (T112). Returns affected count.
        """
        from sqlalchemy import delete

        result = await self.session.execute(
            delete(WhatsAppOptInModel).where(
                WhatsAppOptInModel.customer_id == customer_id
            )
        )
        await self.session.flush()
        return result.rowcount or 0

"""Repository for COD Autopilot delivery-check rows (004-cod-autopilot)."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_delivery_check import (
    WhatsAppDeliveryCheckModel,
)

# Outcomes that end the delivery-check lifecycle — no further sends,
# retries, or fallback closure may touch a row in one of these.
TERMINAL_OUTCOMES = {
    "delivered_confirmed",
    "assumed_delivered",
    "superseded",
}


class WhatsAppDeliveryCheckRepository:
    """Data access for ``whatsapp_delivery_checks``.

    All methods operate on the session's current RLS context — the beat
    tasks scan under RLS bypass and narrow to the row's tenant before
    writing, mirroring ``cod_auto_rto_task``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_order(self, order_id: UUID) -> WhatsAppDeliveryCheckModel | None:
        result = await self.session.execute(
            select(WhatsAppDeliveryCheckModel).where(
                WhatsAppDeliveryCheckModel.order_id == order_id
            )
        )
        return result.scalar_one_or_none()

    async def list_due_sends(
        self, now: datetime, limit: int = 500
    ) -> list[WhatsAppDeliveryCheckModel]:
        """Rows whose next delivery-check message is due."""
        result = await self.session.execute(
            select(WhatsAppDeliveryCheckModel)
            .where(
                WhatsAppDeliveryCheckModel.outcome == "pending",
                WhatsAppDeliveryCheckModel.next_attempt_at.isnot(None),
                WhatsAppDeliveryCheckModel.next_attempt_at <= now,
            )
            .order_by(WhatsAppDeliveryCheckModel.next_attempt_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_fallback_due(
        self, now: datetime, limit: int = 500
    ) -> list[WhatsAppDeliveryCheckModel]:
        """Exhausted rows past their assumed-delivered window (FR-016)."""
        result = await self.session.execute(
            select(WhatsAppDeliveryCheckModel)
            .where(
                WhatsAppDeliveryCheckModel.outcome == "response_exhausted",
                WhatsAppDeliveryCheckModel.assumed_delivered_due_at <= now,
            )
            .order_by(WhatsAppDeliveryCheckModel.assumed_delivered_due_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_exceptions(
        self, store_id: UUID, limit: int = 50, offset: int = 0
    ) -> tuple[list[WhatsAppDeliveryCheckModel], int]:
        """Unresolved exception rows for the merchant queue (FR-021)."""
        from sqlalchemy import func, or_

        base_filter = (
            WhatsAppDeliveryCheckModel.store_id == store_id,
            WhatsAppDeliveryCheckModel.exception_resolved_at.is_(None),
            or_(
                WhatsAppDeliveryCheckModel.outcome == "exception",
                WhatsAppDeliveryCheckModel.exception_reason == "late_contradiction",
            ),
        )
        total = (
            await self.session.execute(
                select(func.count())
                .select_from(WhatsAppDeliveryCheckModel)
                .where(*base_filter)
            )
        ).scalar_one()
        result = await self.session.execute(
            select(WhatsAppDeliveryCheckModel)
            .where(*base_filter)
            .order_by(WhatsAppDeliveryCheckModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), int(total)

    async def create(
        self, model: WhatsAppDeliveryCheckModel
    ) -> WhatsAppDeliveryCheckModel:
        self.session.add(model)
        await self.session.flush()
        return model

    async def list_for_customer(
        self, customer_id: UUID, limit: int = 200
    ) -> list[WhatsAppDeliveryCheckModel]:
        """Delivery-check rows for a customer's orders — DSAR export path
        (research R-13.2)."""
        from src.infrastructure.database.models.tenant.order import OrderModel

        result = await self.session.execute(
            select(WhatsAppDeliveryCheckModel)
            .join(OrderModel, OrderModel.id == WhatsAppDeliveryCheckModel.order_id)
            .where(OrderModel.customer_id == customer_id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def anonymize_for_customer(self, customer_id: UUID) -> int:
        """Erasure path (research R-13.3): blank the raw phone on the
        customer's delivery-check rows and stop any pending automation.
        The rows themselves are order-lifecycle records retained with the
        order; only the PII is removed. Returns rows changed."""
        rows = await self.list_for_customer(customer_id)
        for row in rows:
            row.customer_phone = ""
            if row.outcome in ("pending",):
                row.outcome = "superseded"
            row.next_attempt_at = None
        await self.session.flush()
        return len(rows)

    async def supersede_for_order(self, order_id: UUID) -> bool:
        """Mark an open check ``superseded`` when the order reaches a terminal
        state via any other path (FR-018). Returns True when a row changed.
        Never reopens a terminal row."""
        row = await self.get_by_order(order_id)
        if row is None or row.outcome in TERMINAL_OUTCOMES:
            return False
        was_exception = row.outcome == "exception"
        row.outcome = "superseded"
        row.next_attempt_at = None
        if was_exception and row.exception_resolved_at is None:
            row.exception_resolved_at = datetime.now(UTC)
        await self.session.flush()
        return True

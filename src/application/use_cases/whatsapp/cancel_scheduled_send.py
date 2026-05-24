"""Cancel a pending scheduled WhatsApp send (FR-018) — single + bulk.

Two paths:
- ``execute(send_id)``: cancel one row by id. Returns True if a row moved
  to ``cancelled``, False if no pending row with that id exists.
- ``cancel_by_order(order_id)``: cascade-cancel ALL pending rows linked
  to an order (FR-016 — fires from the OrderStatusChangedEvent handler
  when an order moves to cancelled or refunded). Returns the count.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
    WhatsAppScheduledSendRepository,
)


class CancelScheduledSendUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = WhatsAppScheduledSendRepository(session)

    async def execute(self, send_id: UUID) -> bool:
        return await self.repo.cancel(send_id)

    async def cancel_by_order(self, order_id: UUID) -> int:
        return await self.repo.cancel_by_order(order_id)

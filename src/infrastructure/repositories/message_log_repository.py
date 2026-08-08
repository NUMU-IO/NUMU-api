"""MessageLog repository implementation."""

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.message_log import (
    MessageDirection,
    MessageLog,
    MessageStatus,
)
from src.core.interfaces.repositories.message_log_repository import (
    IMessageLogRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.message_log import MessageLogModel

# Forward-progress ordering for status updates.  FAILED is handled
# specially (always accepted) and is therefore not in this map.
_STATUS_ORDER: dict[MessageStatus, int] = {
    MessageStatus.QUEUED: 0,
    MessageStatus.SENT: 1,
    MessageStatus.DELIVERED: 2,
    MessageStatus.READ: 3,
}


logger = logging.getLogger(__name__)


class MessageLogRepository(IMessageLogRepository):
    """MessageLog repository implementation using SQLAlchemy.

    All queries include an explicit tenant_id filter as a defense-in-depth
    measure alongside PostgreSQL RLS policies.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(MessageLogModel.tenant_id == tid)
        return query

    def _to_entity(self, model: MessageLogModel) -> MessageLog:
        """Convert database model to domain entity."""
        return MessageLog(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            phone=model.phone,
            metadata=model.metadata_,
            message_id=model.message_id,
            direction=model.direction,
            template_name=model.template_name,
            content=model.content,
            status=model.status,
            error_code=model.error_code,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: MessageLog) -> MessageLogModel:
        """Convert domain entity to database model."""
        return MessageLogModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            store_id=entity.store_id,
            phone=entity.phone,
            metadata_=entity.metadata,
            message_id=entity.message_id,
            direction=entity.direction,
            template_name=entity.template_name,
            content=entity.content,
            status=entity.status,
            error_code=entity.error_code,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    # ------------------------------------------------------------------
    # BaseRepository methods
    # ------------------------------------------------------------------

    async def get_by_id(self, entity_id: UUID) -> MessageLog | None:
        """Get message log by ID."""
        query = select(MessageLogModel).where(MessageLogModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[MessageLog]:
        """Get all message logs with pagination."""
        query = (
            select(MessageLogModel)
            .order_by(MessageLogModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: MessageLog) -> MessageLog:
        """Create a message log entry and surface it in the conversation inbox.

        The inbox (``whatsapp_conversations``) was previously only ever written
        from the INBOUND webhook path, so a store could send hundreds of order
        notifications and the merchant's inbox stayed empty — five logged
        messages, zero threads. From the merchant's point of view the product
        looked broken while working perfectly.

        Threading it here rather than at each call site is deliberate: every
        transport and every notification route already funnels through this
        method, so one place catches all of them and none can forget. Best
        effort — a thread is a convenience, and failing to write one must never
        fail the message it describes.
        """
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        await self._touch_conversation(entity)
        return self._to_entity(model)

    async def _touch_conversation(self, entity: MessageLog) -> None:
        """Create/update the inbox thread for this message. Never raises."""
        if not entity.store_id or not entity.tenant_id or not entity.phone:
            return
        try:
            from src.infrastructure.repositories.whatsapp_conversation_repository import (  # noqa: E501
                WhatsAppConversationRepository,
            )

            direction = str(getattr(entity.direction, "value", entity.direction))
            # Prefer the human-readable body; fall back to the template name so
            # a thread never previews as an empty line.
            preview = (entity.content or "").strip() or entity.template_name or ""
            await WhatsAppConversationRepository(self.session).upsert_on_message(
                store_id=entity.store_id,
                tenant_id=entity.tenant_id,
                phone=entity.phone,
                name=None,
                message_preview=preview,
                direction=direction,
            )
        except Exception:
            logger.warning("conversation_touch_failed", exc_info=True)

    async def update(self, entity: MessageLog) -> MessageLog:
        """Update an existing message log entry."""
        query = select(MessageLogModel).where(MessageLogModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            model.phone = entity.phone
            model.metadata_ = entity.metadata
            model.message_id = entity.message_id
            model.direction = entity.direction
            model.template_name = entity.template_name
            model.content = entity.content
            model.status = entity.status
            model.error_code = entity.error_code
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"MessageLog with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a message log entry by ID."""
        query = select(MessageLogModel).where(MessageLogModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of message logs."""
        query = select(func.count(MessageLogModel.id))
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

    # ------------------------------------------------------------------
    # Custom methods
    # ------------------------------------------------------------------

    async def get_by_message_id(self, message_id: str) -> MessageLog | None:
        """Get a message log entry by its provider message ID."""
        query = select(MessageLogModel).where(MessageLogModel.message_id == message_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_store(
        self,
        store_id: UUID,
        direction: MessageDirection | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[MessageLog]:
        """Get message logs for a store, optionally filtered by direction."""
        query = select(MessageLogModel).where(MessageLogModel.store_id == store_id)
        if direction is not None:
            query = query.where(MessageLogModel.direction == direction)
        query = (
            query.order_by(MessageLogModel.created_at.desc()).offset(skip).limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_phone(
        self,
        store_id: UUID,
        phone: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[MessageLog]:
        """Get message logs for a specific phone number within a store."""
        query = (
            select(MessageLogModel)
            .where(
                MessageLogModel.store_id == store_id,
                MessageLogModel.phone == phone,
            )
            .order_by(MessageLogModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_latest_by_phone(self, phone: str) -> MessageLog | None:
        """Get the most recent message log entry for a phone number.

        Intentionally skips the tenant filter so webhook handlers that
        operate without tenant context can resolve store/tenant from
        prior messages.
        """
        query = (
            select(MessageLogModel)
            .where(MessageLogModel.phone == phone)
            .order_by(MessageLogModel.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def update_status(
        self,
        message_id: str,
        status: MessageStatus,
        error_code: str | None = None,
    ) -> MessageLog | None:
        """Update the delivery status of a message by its provider message ID.

        Applies a regression guard: the status is only updated if the new
        status represents forward progress (QUEUED→SENT→DELIVERED→READ).
        FAILED is always accepted regardless of current status.
        """
        query = select(MessageLogModel).where(MessageLogModel.message_id == message_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return None

        # Regression guard: only move forward, but always accept FAILED.
        if status != MessageStatus.FAILED:
            new_order = _STATUS_ORDER.get(status, 0)
            current_order = _STATUS_ORDER.get(model.status, 0)
            if new_order <= current_order:
                return self._to_entity(model)

        model.status = status
        model.error_code = error_code
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

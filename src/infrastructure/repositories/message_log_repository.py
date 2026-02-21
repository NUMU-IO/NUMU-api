"""MessageLog repository implementation."""

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
        """Create a new message log entry."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

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
        query = select(MessageLogModel).where(
            MessageLogModel.message_id == message_id
        )
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
        query = select(MessageLogModel).where(
            MessageLogModel.store_id == store_id
        )
        if direction is not None:
            query = query.where(MessageLogModel.direction == direction)
        query = (
            query.order_by(MessageLogModel.created_at.desc())
            .offset(skip)
            .limit(limit)
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

    async def update_status(
        self,
        message_id: str,
        status: MessageStatus,
        error_code: str | None = None,
    ) -> MessageLog | None:
        """Update the delivery status of a message by its provider message ID."""
        query = select(MessageLogModel).where(
            MessageLogModel.message_id == message_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        model.status = status
        model.error_code = error_code
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

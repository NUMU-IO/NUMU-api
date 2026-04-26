"""EmailLog repository implementation.

Plain SQLAlchemy CRUD — no caching layer. Email-log writes happen on the
critical path of every send, so we keep the repository simple and let
PostgreSQL be the source of truth.
"""

from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.email_log import EmailLog, EmailStatus
from src.core.interfaces.repositories.email_log_repository import (
    IEmailLogRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.email_log import EmailLogModel


class EmailLogRepository(IEmailLogRepository):
    """SQLAlchemy implementation of :class:`IEmailLogRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(EmailLogModel.tenant_id == tid)
        return query

    def _to_entity(self, model: EmailLogModel) -> EmailLog:
        return EmailLog(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            recipient=model.recipient,
            message_id=model.message_id,
            event_type=model.event_type,
            template_id=model.template_id,
            language=model.language,
            subject=model.subject,
            status=cast(EmailStatus, model.status),
            error_code=model.error_code,
            used_custom_template=model.used_custom_template,
            extra_data=model.extra_data or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: EmailLog) -> EmailLogModel:
        return EmailLogModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            recipient=entity.recipient,
            message_id=entity.message_id,
            event_type=entity.event_type,
            template_id=entity.template_id,
            language=entity.language,
            subject=entity.subject,
            status=entity.status,
            error_code=entity.error_code,
            used_custom_template=entity.used_custom_template,
            extra_data=entity.extra_data or None,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    # ------------------------------------------------------------------
    # BaseRepository methods
    # ------------------------------------------------------------------

    async def get_by_id(self, entity_id: UUID) -> EmailLog | None:
        query = select(EmailLogModel).where(EmailLogModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[EmailLog]:
        query = (
            select(EmailLogModel)
            .order_by(EmailLogModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: EmailLog) -> EmailLog:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: EmailLog) -> EmailLog:
        query = select(EmailLogModel).where(EmailLogModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"EmailLog with id {entity.id} not found")

        model.recipient = entity.recipient
        model.message_id = entity.message_id
        model.event_type = entity.event_type
        model.template_id = entity.template_id
        model.language = entity.language
        model.subject = entity.subject
        model.status = entity.status
        model.error_code = entity.error_code
        model.used_custom_template = entity.used_custom_template
        model.extra_data = entity.extra_data or None

        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        query = select(EmailLogModel).where(EmailLogModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def count(self) -> int:
        query = select(func.count(EmailLogModel.id))
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

    # ------------------------------------------------------------------
    # Custom methods
    # ------------------------------------------------------------------

    async def list_by_store(
        self,
        store_id: UUID,
        event_type: str | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[EmailLog]:
        query = select(EmailLogModel).where(EmailLogModel.store_id == store_id)
        if event_type is not None:
            query = query.where(EmailLogModel.event_type == event_type)
        if status is not None:
            query = query.where(EmailLogModel.status == status)
        query = (
            query.order_by(EmailLogModel.created_at.desc()).offset(skip).limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_message_id(self, message_id: str) -> EmailLog | None:
        query = select(EmailLogModel).where(EmailLogModel.message_id == message_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def count_by_store(
        self,
        store_id: UUID,
        event_type: str | None = None,
        status: str | None = None,
    ) -> int:
        query = select(func.count(EmailLogModel.id)).where(
            EmailLogModel.store_id == store_id
        )
        if event_type is not None:
            query = query.where(EmailLogModel.event_type == event_type)
        if status is not None:
            query = query.where(EmailLogModel.status == status)
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0


# Public alias matching the naming convention used elsewhere in the
# repositories package.
EmailLogRepositoryImpl = EmailLogRepository

"""WhatsApp template repository implementation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.whatsapp_template import (
    TemplateCategory,
    TemplateStatus,
    WhatsAppTemplate,
)
from src.core.interfaces.repositories.whatsapp_template_repository import (
    WhatsAppTemplateRepository,
)
from src.infrastructure.database.models import WhatsAppTemplateModel


class WhatsAppTemplateRepositoryImpl(WhatsAppTemplateRepository):
    """WhatsApp template repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: WhatsAppTemplateModel) -> WhatsAppTemplate:
        return WhatsAppTemplate(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            channel_connection_id=model.channel_connection_id,
            external_template_id=model.external_template_id,
            name=model.name,
            category=TemplateCategory(model.category),
            language=model.language,
            status=TemplateStatus(model.status),
            components=model.components or {},
            rejection_reason=model.rejection_reason,
            submitted_at=model.submitted_at,
            approved_at=model.approved_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> WhatsAppTemplate | None:
        result = await self.session.execute(
            select(WhatsAppTemplateModel).where(WhatsAppTemplateModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[WhatsAppTemplate]:
        result = await self.session.execute(
            select(WhatsAppTemplateModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: WhatsAppTemplate) -> WhatsAppTemplate:
        model = WhatsAppTemplateModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            store_id=entity.store_id,
            channel_connection_id=entity.channel_connection_id,
            external_template_id=entity.external_template_id,
            name=entity.name,
            category=entity.category.value,
            language=entity.language,
            status=entity.status.value,
            components=entity.components,
            rejection_reason=entity.rejection_reason,
            submitted_at=entity.submitted_at,
            approved_at=entity.approved_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        self.session.add(model)
        await self.session.flush()
        return entity

    async def update(self, entity: WhatsAppTemplate) -> WhatsAppTemplate:
        result = await self.session.execute(
            select(WhatsAppTemplateModel).where(WhatsAppTemplateModel.id == entity.id)
        )
        model = result.scalar_one()
        model.external_template_id = entity.external_template_id
        model.name = entity.name
        model.category = entity.category.value
        model.language = entity.language
        model.status = entity.status.value
        model.components = entity.components
        model.rejection_reason = entity.rejection_reason
        model.submitted_at = entity.submitted_at
        model.approved_at = entity.approved_at
        model.updated_at = entity.updated_at
        await self.session.flush()
        return entity

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(WhatsAppTemplateModel).where(WhatsAppTemplateModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(WhatsAppTemplateModel))
        return len(result.scalars().all())

    async def get_by_connection_and_name(
        self,
        channel_connection_id: UUID,
        name: str,
        language: str,
    ) -> WhatsAppTemplate | None:
        result = await self.session.execute(
            select(WhatsAppTemplateModel).where(
                WhatsAppTemplateModel.channel_connection_id == channel_connection_id,
                WhatsAppTemplateModel.name == name,
                WhatsAppTemplateModel.language == language,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_connection(
        self,
        channel_connection_id: UUID,
        status: TemplateStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[WhatsAppTemplate]:
        query = select(WhatsAppTemplateModel).where(
            WhatsAppTemplateModel.channel_connection_id == channel_connection_id
        )
        if status:
            query = query.where(WhatsAppTemplateModel.status == status.value)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_pending(
        self, skip: int = 0, limit: int = 100
    ) -> list[WhatsAppTemplate]:
        result = await self.session.execute(
            select(WhatsAppTemplateModel)
            .where(WhatsAppTemplateModel.status == TemplateStatus.PENDING.value)
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update_status(
        self,
        template_id: UUID,
        status: TemplateStatus,
        rejection_reason: str | None = None,
    ) -> WhatsAppTemplate | None:
        result = await self.session.execute(
            select(WhatsAppTemplateModel).where(WhatsAppTemplateModel.id == template_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.status = status.value
        model.rejection_reason = rejection_reason
        await self.session.flush()
        return self._to_entity(model)

    async def list_by_store(
        self,
        store_id: UUID,
        category: TemplateCategory | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[WhatsAppTemplate]:
        query = select(WhatsAppTemplateModel).where(
            WhatsAppTemplateModel.store_id == store_id
        )
        if category:
            query = query.where(WhatsAppTemplateModel.category == category.value)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

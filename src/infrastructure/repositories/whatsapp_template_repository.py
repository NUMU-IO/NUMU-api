"""Repository for WhatsApp message templates."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)


class WhatsAppTemplateRepository:
    """CRUD operations for WhatsApp templates."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, model: WhatsAppTemplateModel) -> WhatsAppTemplateModel:
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return model

    async def get_by_id(self, template_id: UUID) -> WhatsAppTemplateModel | None:
        result = await self.session.execute(
            select(WhatsAppTemplateModel).where(WhatsAppTemplateModel.id == template_id)
        )
        return result.scalar_one_or_none()

    async def list_by_store(
        self,
        store_id: UUID,
        category: str | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[WhatsAppTemplateModel], int]:
        query = select(WhatsAppTemplateModel).where(
            WhatsAppTemplateModel.store_id == store_id
        )
        count_query = select(func.count(WhatsAppTemplateModel.id)).where(
            WhatsAppTemplateModel.store_id == store_id
        )
        if category:
            query = query.where(WhatsAppTemplateModel.category == category)
            count_query = count_query.where(WhatsAppTemplateModel.category == category)
        if status:
            query = query.where(WhatsAppTemplateModel.status == status)
            count_query = count_query.where(WhatsAppTemplateModel.status == status)

        query = (
            query.order_by(WhatsAppTemplateModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(query)
        total = (await self.session.execute(count_query)).scalar() or 0
        return list(result.scalars().all()), total

    async def get_by_name(
        self, store_id: UUID, name: str, language: str
    ) -> WhatsAppTemplateModel | None:
        result = await self.session.execute(
            select(WhatsAppTemplateModel).where(
                WhatsAppTemplateModel.store_id == store_id,
                WhatsAppTemplateModel.name == name,
                WhatsAppTemplateModel.language == language,
            )
        )
        return result.scalar_one_or_none()

    async def update_status(
        self, template_id: UUID, status: str, rejection_reason: str | None = None
    ) -> WhatsAppTemplateModel | None:
        model = await self.get_by_id(template_id)
        if not model:
            return None
        model.status = status
        if rejection_reason is not None:
            model.rejection_reason = rejection_reason
        if status == "APPROVED":
            from datetime import UTC, datetime

            model.approved_at = datetime.now(UTC)
        await self.session.flush()
        await self.session.refresh(model)
        return model

    async def delete(self, template_id: UUID) -> bool:
        model = await self.get_by_id(template_id)
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

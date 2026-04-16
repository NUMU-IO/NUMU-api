"""Repository for WhatsApp broadcast campaigns."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_campaign import (
    WhatsAppCampaignModel,
    WhatsAppCampaignRecipientModel,
)


class WhatsAppCampaignRepository:
    """CRUD operations for WhatsApp campaigns."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, model: WhatsAppCampaignModel) -> WhatsAppCampaignModel:
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return model

    async def get_by_id(self, campaign_id: UUID) -> WhatsAppCampaignModel | None:
        result = await self.session.execute(
            select(WhatsAppCampaignModel).where(WhatsAppCampaignModel.id == campaign_id)
        )
        return result.scalar_one_or_none()

    async def list_by_store(
        self,
        store_id: UUID,
        status: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[WhatsAppCampaignModel], int]:
        query = select(WhatsAppCampaignModel).where(
            WhatsAppCampaignModel.store_id == store_id
        )
        count_query = select(func.count(WhatsAppCampaignModel.id)).where(
            WhatsAppCampaignModel.store_id == store_id
        )
        if status:
            query = query.where(WhatsAppCampaignModel.status == status)
            count_query = count_query.where(WhatsAppCampaignModel.status == status)

        query = (
            query.order_by(WhatsAppCampaignModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(query)
        total = (await self.session.execute(count_query)).scalar() or 0
        return list(result.scalars().all()), total

    async def update_status(self, campaign_id: UUID, status: str, **kwargs) -> None:
        values = {"status": status, **kwargs}
        await self.session.execute(
            update(WhatsAppCampaignModel)
            .where(WhatsAppCampaignModel.id == campaign_id)
            .values(**values)
        )
        await self.session.flush()

    async def increment_counter(
        self, campaign_id: UUID, field: str, amount: int = 1
    ) -> None:
        col = getattr(WhatsAppCampaignModel, field)
        await self.session.execute(
            update(WhatsAppCampaignModel)
            .where(WhatsAppCampaignModel.id == campaign_id)
            .values({field: col + amount})
        )
        await self.session.flush()

    async def delete(self, campaign_id: UUID) -> bool:
        model = await self.get_by_id(campaign_id)
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    # ── Recipients ──

    async def add_recipient(
        self, model: WhatsAppCampaignRecipientModel
    ) -> WhatsAppCampaignRecipientModel:
        self.session.add(model)
        await self.session.flush()
        return model

    async def add_recipients_bulk(
        self, recipients: list[WhatsAppCampaignRecipientModel]
    ) -> None:
        self.session.add_all(recipients)
        await self.session.flush()

    async def list_recipients(
        self,
        campaign_id: UUID,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[WhatsAppCampaignRecipientModel], int]:
        query = select(WhatsAppCampaignRecipientModel).where(
            WhatsAppCampaignRecipientModel.campaign_id == campaign_id
        )
        count_query = select(func.count(WhatsAppCampaignRecipientModel.id)).where(
            WhatsAppCampaignRecipientModel.campaign_id == campaign_id
        )
        if status:
            query = query.where(WhatsAppCampaignRecipientModel.status == status)
            count_query = count_query.where(
                WhatsAppCampaignRecipientModel.status == status
            )
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        total = (await self.session.execute(count_query)).scalar() or 0
        return list(result.scalars().all()), total

    async def update_recipient_status(
        self, message_id: str, status: str
    ) -> WhatsAppCampaignRecipientModel | None:
        result = await self.session.execute(
            select(WhatsAppCampaignRecipientModel).where(
                WhatsAppCampaignRecipientModel.message_id == message_id
            )
        )
        model = result.scalar_one_or_none()
        if model:
            model.status = status
            await self.session.flush()
        return model

    async def get_pending_recipients(
        self, campaign_id: UUID, limit: int = 100
    ) -> list[WhatsAppCampaignRecipientModel]:
        result = await self.session.execute(
            select(WhatsAppCampaignRecipientModel)
            .where(
                WhatsAppCampaignRecipientModel.campaign_id == campaign_id,
                WhatsAppCampaignRecipientModel.status == "pending",
            )
            .limit(limit)
        )
        return list(result.scalars().all())

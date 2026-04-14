"""Repository for WhatsApp conversations (chat inbox)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.whatsapp_conversation import (
    WhatsAppConversationModel,
)


class WhatsAppConversationRepository:
    """CRUD operations for WhatsApp conversations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_store(
        self,
        store_id: UUID,
        status: str | None = None,
        unread_only: bool = False,
        search: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[WhatsAppConversationModel], int]:
        query = select(WhatsAppConversationModel).where(
            WhatsAppConversationModel.store_id == store_id
        )
        count_query = select(func.count(WhatsAppConversationModel.id)).where(
            WhatsAppConversationModel.store_id == store_id
        )

        if status:
            query = query.where(WhatsAppConversationModel.status == status)
            count_query = count_query.where(WhatsAppConversationModel.status == status)
        if unread_only:
            query = query.where(WhatsAppConversationModel.unread_count > 0)
            count_query = count_query.where(WhatsAppConversationModel.unread_count > 0)
        if search:
            like_pat = f"%{search}%"
            query = query.where(
                WhatsAppConversationModel.customer_name.ilike(like_pat)
                | WhatsAppConversationModel.customer_phone.ilike(like_pat)
            )
            count_query = count_query.where(
                WhatsAppConversationModel.customer_name.ilike(like_pat)
                | WhatsAppConversationModel.customer_phone.ilike(like_pat)
            )

        query = (
            query.order_by(WhatsAppConversationModel.last_message_at.desc().nullslast())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(query)
        total = (await self.session.execute(count_query)).scalar() or 0
        return list(result.scalars().all()), total

    async def get_by_id(
        self, conversation_id: UUID
    ) -> WhatsAppConversationModel | None:
        result = await self.session.execute(
            select(WhatsAppConversationModel).where(
                WhatsAppConversationModel.id == conversation_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_phone(
        self, store_id: UUID, phone: str
    ) -> WhatsAppConversationModel | None:
        result = await self.session.execute(
            select(WhatsAppConversationModel).where(
                WhatsAppConversationModel.store_id == store_id,
                WhatsAppConversationModel.customer_phone == phone,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_on_message(
        self,
        store_id: UUID,
        tenant_id: UUID,
        phone: str,
        name: str | None,
        message_preview: str | None,
        direction: str,
        customer_id: UUID | None = None,
    ) -> WhatsAppConversationModel:
        """Create or update a conversation when a message arrives."""
        conv = await self.get_by_phone(store_id, phone)
        now = datetime.now(UTC)

        if conv:
            conv.last_message_at = now
            conv.last_message_preview = (message_preview or "")[:255]
            conv.last_message_direction = direction
            if name and not conv.customer_name:
                conv.customer_name = name
            if customer_id and not conv.customer_id:
                conv.customer_id = customer_id
            if direction == "inbound":
                conv.unread_count = (conv.unread_count or 0) + 1
                conv.window_expires_at = now + timedelta(hours=24)
            if conv.status == "archived":
                conv.status = "active"
            await self.session.flush()
            await self.session.refresh(conv)
            return conv

        conv = WhatsAppConversationModel(
            store_id=store_id,
            tenant_id=tenant_id,
            customer_id=customer_id,
            customer_phone=phone,
            customer_name=name,
            last_message_at=now,
            last_message_preview=(message_preview or "")[:255],
            last_message_direction=direction,
            unread_count=1 if direction == "inbound" else 0,
            status="active",
            window_expires_at=(now + timedelta(hours=24))
            if direction == "inbound"
            else None,
        )
        self.session.add(conv)
        await self.session.flush()
        await self.session.refresh(conv)
        return conv

    async def mark_read(self, conversation_id: UUID) -> None:
        await self.session.execute(
            update(WhatsAppConversationModel)
            .where(WhatsAppConversationModel.id == conversation_id)
            .values(unread_count=0)
        )
        await self.session.flush()

    async def update_status(self, conversation_id: UUID, status: str) -> None:
        await self.session.execute(
            update(WhatsAppConversationModel)
            .where(WhatsAppConversationModel.id == conversation_id)
            .values(status=status)
        )
        await self.session.flush()

    async def assign(self, conversation_id: UUID, user_id: UUID | None) -> None:
        await self.session.execute(
            update(WhatsAppConversationModel)
            .where(WhatsAppConversationModel.id == conversation_id)
            .values(assigned_to=user_id)
        )
        await self.session.flush()

    async def get_unread_count(self, store_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count(WhatsAppConversationModel.id)).where(
                WhatsAppConversationModel.store_id == store_id,
                WhatsAppConversationModel.unread_count > 0,
                WhatsAppConversationModel.status == "active",
            )
        )
        return result.scalar() or 0

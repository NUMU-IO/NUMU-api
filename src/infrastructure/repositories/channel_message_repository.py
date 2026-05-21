"""Channel message repository implementation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.channel_connection import ChannelType
from src.core.entities.channel_message import (
    ChannelMessage,
    MessageDirection,
    MessageStatus,
)
from src.core.interfaces.repositories.channel_message_repository import (
    ChannelMessageRepository,
)
from src.infrastructure.database.models import ChannelMessageModel


class ChannelMessageRepositoryImpl(ChannelMessageRepository):
    """Channel message repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: ChannelMessageModel) -> ChannelMessage:
        return ChannelMessage(
            id=model.id,
            tenant_id=model.tenant_id,
            thread_id=model.thread_id,
            direction=MessageDirection(model.direction),
            channel=ChannelType(model.channel),
            external_message_id=model.external_message_id,
            external_timestamp=model.external_timestamp,
            sender_external_id=model.sender_external_id,
            type=model.type,
            body=model.body,
            attachment_url=model.attachment_url,
            attachment_mime=model.attachment_mime,
            template_name=model.template_name,
            template_payload=model.template_payload,
            product_id=model.product_id,
            status=MessageStatus(model.status),
            error_code=model.error_code,
            error_message=model.error_message,
            raw_payload=model.raw_payload or {},
            created_at=model.created_at,
        )

    async def get_by_id(self, entity_id: UUID) -> ChannelMessage | None:
        result = await self.session.execute(
            select(ChannelMessageModel).where(ChannelMessageModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[ChannelMessage]:
        result = await self.session.execute(
            select(ChannelMessageModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: ChannelMessage) -> ChannelMessage:
        model = ChannelMessageModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            thread_id=entity.thread_id,
            direction=entity.direction.value,
            channel=entity.channel.value,
            external_message_id=entity.external_message_id,
            external_timestamp=entity.external_timestamp,
            sender_external_id=entity.sender_external_id,
            type=entity.type.value,
            body=entity.body,
            attachment_url=entity.attachment_url,
            attachment_mime=entity.attachment_mime,
            template_name=entity.template_name,
            template_payload=entity.template_payload,
            product_id=entity.product_id,
            status=entity.status.value,
            error_code=entity.error_code,
            error_message=entity.error_message,
            raw_payload=entity.raw_payload,
            created_at=entity.created_at,
        )
        self.session.add(model)
        await self.session.flush()
        return entity

    async def update(self, entity: ChannelMessage) -> ChannelMessage:
        result = await self.session.execute(
            select(ChannelMessageModel).where(ChannelMessageModel.id == entity.id)
        )
        model = result.scalar_one()
        model.status = entity.status.value
        model.error_code = entity.error_code
        model.error_message = entity.error_message
        model.updated_at = entity.updated_at
        await self.session.flush()
        return entity

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(ChannelMessageModel).where(ChannelMessageModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(ChannelMessageModel))
        return len(result.scalars().all())

    async def get_by_external_id(
        self,
        channel: ChannelType,
        external_message_id: str,
    ) -> ChannelMessage | None:
        result = await self.session.execute(
            select(ChannelMessageModel).where(
                ChannelMessageModel.channel == channel.value,
                ChannelMessageModel.external_message_id == external_message_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_thread(
        self,
        thread_id: UUID,
        cursor: str | None = None,
        limit: int = 100,
    ) -> list[ChannelMessage]:
        query = select(ChannelMessageModel).where(
            ChannelMessageModel.thread_id == thread_id
        )
        if cursor:
            from datetime import datetime

            cursor_dt = datetime.fromisoformat(cursor)
            query = query.where(ChannelMessageModel.external_timestamp < cursor_dt)
        query = query.order_by(ChannelMessageModel.created_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update_status(
        self,
        message_id: UUID,
        status: MessageStatus,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ChannelMessage | None:
        result = await self.session.execute(
            select(ChannelMessageModel).where(ChannelMessageModel.id == message_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.status = status.value
        model.error_code = error_code
        model.error_message = error_message
        await self.session.flush()
        return self._to_entity(model)

    async def count_by_thread(self, thread_id: UUID) -> int:
        result = await self.session.execute(
            select(ChannelMessageModel).where(
                ChannelMessageModel.thread_id == thread_id
            )
        )
        return len(result.scalars().all())

    async def get_latest_by_thread(
        self,
        thread_id: UUID,
        direction: MessageDirection | None = None,
    ) -> ChannelMessage | None:
        query = select(ChannelMessageModel).where(
            ChannelMessageModel.thread_id == thread_id
        )
        if direction:
            query = query.where(ChannelMessageModel.direction == direction.value)
        query = query.order_by(ChannelMessageModel.created_at.desc()).limit(1)
        result = await self.session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

"""Message thread repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.channel_connection import ChannelType
from src.core.entities.message_thread import MessageThread, ThreadStatus
from src.core.interfaces.repositories.message_thread_repository import (
    MessageThreadRepository,
)
from src.infrastructure.database.models import MessageThreadModel


class MessageThreadRepositoryImpl(MessageThreadRepository):
    """Message thread repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: MessageThreadModel) -> MessageThread:
        return MessageThread(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            channel=ChannelType(model.channel),
            channel_connection_id=model.channel_connection_id,
            external_participant_id=model.external_participant_id,
            participant_name=model.participant_name,
            participant_avatar_url=model.participant_avatar_url,
            participant_phone_e164=model.participant_phone_e164,
            status=ThreadStatus(model.status),
            last_message_at=model.last_message_at,
            last_message_preview=model.last_message_preview,
            unread_count=model.unread_count,
            assigned_user_id=model.assigned_user_id,
            metadata=model.thread_metadata or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> MessageThread | None:
        result = await self.session.execute(
            select(MessageThreadModel).where(MessageThreadModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 50) -> list[MessageThread]:
        result = await self.session.execute(
            select(MessageThreadModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: MessageThread) -> MessageThread:
        model = MessageThreadModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            store_id=entity.store_id,
            channel=entity.channel.value,
            channel_connection_id=entity.channel_connection_id,
            external_participant_id=entity.external_participant_id,
            participant_name=entity.participant_name,
            participant_avatar_url=entity.participant_avatar_url,
            participant_phone_e164=entity.participant_phone_e164,
            status=entity.status.value,
            last_message_at=entity.last_message_at,
            last_message_preview=entity.last_message_preview,
            unread_count=entity.unread_count,
            assigned_user_id=entity.assigned_user_id,
            thread_metadata=entity.metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        self.session.add(model)
        await self.session.flush()
        return entity

    async def update(self, entity: MessageThread) -> MessageThread:
        result = await self.session.execute(
            select(MessageThreadModel).where(MessageThreadModel.id == entity.id)
        )
        model = result.scalar_one()
        model.status = entity.status.value
        model.last_message_at = entity.last_message_at
        model.last_message_preview = entity.last_message_preview
        model.unread_count = entity.unread_count
        model.assigned_user_id = entity.assigned_user_id
        model.thread_metadata = entity.metadata
        model.updated_at = entity.updated_at
        await self.session.flush()
        return entity

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(MessageThreadModel).where(MessageThreadModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(MessageThreadModel))
        return len(result.scalars().all())

    async def get_by_connection_and_participant(
        self,
        channel_connection_id: UUID,
        external_participant_id: str,
    ) -> MessageThread | None:
        result = await self.session.execute(
            select(MessageThreadModel).where(
                MessageThreadModel.channel_connection_id == channel_connection_id,
                MessageThreadModel.external_participant_id == external_participant_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_store(
        self,
        store_id: UUID,
        channel: ChannelType | None = None,
        status: ThreadStatus | None = None,
        unread_only: bool = False,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[MessageThread]:
        query = select(MessageThreadModel).where(
            MessageThreadModel.store_id == store_id
        )
        if channel:
            query = query.where(MessageThreadModel.channel == channel.value)
        if status:
            query = query.where(MessageThreadModel.status == status.value)
        if unread_only:
            query = query.where(MessageThreadModel.unread_count > 0)
        if search:
            query = query.where(
                MessageThreadModel.participant_name.ilike(f"%{search}%")
            )
        if cursor:
            # cursor is ISO timestamp of last_message_at
            from datetime import datetime

            cursor_dt = datetime.fromisoformat(cursor)
            query = query.where(MessageThreadModel.last_message_at < cursor_dt)
        query = query.order_by(MessageThreadModel.last_message_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_unread(self, store_id: UUID) -> int:
        result = await self.session.execute(
            select(MessageThreadModel).where(
                MessageThreadModel.store_id == store_id,
                MessageThreadModel.unread_count > 0,
            )
        )
        return len(result.scalars().all())

    async def update_status(
        self,
        thread_id: UUID,
        status: ThreadStatus,
    ) -> MessageThread | None:
        result = await self.session.execute(
            select(MessageThreadModel).where(MessageThreadModel.id == thread_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.status = status.value
        await self.session.flush()
        return self._to_entity(model)

    async def mark_read(self, thread_id: UUID) -> MessageThread | None:
        result = await self.session.execute(
            select(MessageThreadModel).where(MessageThreadModel.id == thread_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.unread_count = 0
        await self.session.flush()
        return self._to_entity(model)

    async def increment_unread(self, thread_id: UUID) -> MessageThread | None:
        result = await self.session.execute(
            select(MessageThreadModel).where(MessageThreadModel.id == thread_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.unread_count = (model.unread_count or 0) + 1
        await self.session.flush()
        return self._to_entity(model)

    async def update_last_message(
        self,
        thread_id: UUID,
        message_preview: str,
        message_at: datetime,
    ) -> MessageThread | None:
        result = await self.session.execute(
            select(MessageThreadModel).where(MessageThreadModel.id == thread_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.last_message_preview = message_preview
        model.last_message_at = message_at
        await self.session.flush()
        return self._to_entity(model)

"""Webhook event repository implementation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.webhook_event import (
    WebhookEvent,
    WebhookProvider,
    WebhookStatus,
)
from src.core.interfaces.repositories.webhook_event_repository import (
    WebhookEventRepository,
)
from src.infrastructure.database.models import WebhookEventModel


class WebhookEventRepositoryImpl(WebhookEventRepository):
    """Webhook event repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: WebhookEventModel) -> WebhookEvent:
        return WebhookEvent(
            id=model.id,
            provider=WebhookProvider(model.provider),
            event_type=model.event_type,
            external_id=model.external_id,
            payload=model.payload or {},
            signature=model.signature,
            received_at=model.received_at,
            processed_at=model.processed_at,
            status=WebhookStatus(model.status),
            error=model.error,
            retry_count=model.retry_count,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> WebhookEvent | None:
        result = await self.session.execute(
            select(WebhookEventModel).where(WebhookEventModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[WebhookEvent]:
        result = await self.session.execute(
            select(WebhookEventModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: WebhookEvent) -> WebhookEvent:
        model = WebhookEventModel(
            id=entity.id,
            provider=entity.provider.value,
            event_type=entity.event_type,
            external_id=entity.external_id,
            payload=entity.payload,
            signature=entity.signature,
            received_at=entity.received_at,
            processed_at=entity.processed_at,
            status=entity.status.value,
            error=entity.error,
            retry_count=entity.retry_count,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        self.session.add(model)
        await self.session.flush()
        return entity

    async def update(self, entity: WebhookEvent) -> WebhookEvent:
        result = await self.session.execute(
            select(WebhookEventModel).where(WebhookEventModel.id == entity.id)
        )
        model = result.scalar_one()
        model.provider = entity.provider.value
        model.event_type = entity.event_type
        model.external_id = entity.external_id
        model.payload = entity.payload
        model.signature = entity.signature
        model.received_at = entity.received_at
        model.processed_at = entity.processed_at
        model.status = entity.status.value
        model.error = entity.error
        model.retry_count = entity.retry_count
        model.updated_at = entity.updated_at
        await self.session.flush()
        return entity

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(WebhookEventModel).where(WebhookEventModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(WebhookEventModel))
        return len(result.scalars().all())

    async def get_by_external_id(
        self,
        provider: WebhookProvider,
        external_id: str,
    ) -> WebhookEvent | None:
        result = await self.session.execute(
            select(WebhookEventModel).where(
                WebhookEventModel.provider == provider.value,
                WebhookEventModel.external_id == external_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_provider(
        self,
        provider: WebhookProvider,
        status: WebhookStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[WebhookEvent]:
        query = select(WebhookEventModel).where(
            WebhookEventModel.provider == provider.value
        )
        if status:
            query = query.where(WebhookEventModel.status == status.value)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_failed(
        self, max_retries: int = 3, limit: int = 100
    ) -> list[WebhookEvent]:
        result = await self.session.execute(
            select(WebhookEventModel)
            .where(
                WebhookEventModel.status == WebhookStatus.FAILED.value,
                WebhookEventModel.retry_count < max_retries,
            )
            .limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update_status(
        self,
        event_id: UUID,
        status: WebhookStatus,
        error: str | None = None,
    ) -> WebhookEvent | None:
        result = await self.session.execute(
            select(WebhookEventModel).where(WebhookEventModel.id == event_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        model.status = status.value
        model.error = error
        if status == WebhookStatus.PROCESSED:
            model.processed_at = model.updated_at
        await self.session.flush()
        return self._to_entity(model)

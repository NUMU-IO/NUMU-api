"""Webhook delivery log repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.webhook import (
    WebhookDeliveryLog,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookDeliveryLogRepository,
)
from src.infrastructure.database.models.tenant.webhook import WebhookDeliveryLogModel


class WebhookDeliveryLogRepository(IWebhookDeliveryLogRepository):
    """Webhook delivery log repository using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: WebhookDeliveryLogModel) -> WebhookDeliveryLog:
        return WebhookDeliveryLog(
            id=model.id,
            subscription_id=model.subscription_id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            event_type=WebhookEventType(model.event_type),
            event_id=model.event_id,
            payload=model.payload or {},
            status=WebhookDeliveryStatus(model.status),
            attempt_count=model.attempt_count,
            next_attempt_at=model.next_attempt_at,
            last_attempt_at=model.last_attempt_at,
            last_status_code=model.last_status_code,
            last_response_body=model.last_response_body,
            last_error=model.last_error,
            exhausted_at=model.exhausted_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> WebhookDeliveryLog | None:
        result = await self.session.get(WebhookDeliveryLogModel, entity_id)
        return self._to_entity(result) if result else None

    async def get_all(
        self, skip: int = 0, limit: int = 100
    ) -> list[WebhookDeliveryLog]:
        query = (
            select(WebhookDeliveryLogModel)
            .order_by(WebhookDeliveryLogModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_pending_retries(
        self, now: datetime, limit: int = 100
    ) -> list[WebhookDeliveryLog]:
        """Fetch logs due for retry — drives the Celery beat poller."""
        query = (
            select(WebhookDeliveryLogModel)
            .where(
                WebhookDeliveryLogModel.status == WebhookDeliveryStatus.PENDING,
                WebhookDeliveryLogModel.next_attempt_at <= now,
            )
            .order_by(WebhookDeliveryLogModel.next_attempt_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_subscription(
        self,
        subscription_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[WebhookDeliveryLog]:
        query = (
            select(WebhookDeliveryLogModel)
            .where(WebhookDeliveryLogModel.subscription_id == subscription_id)
            .order_by(WebhookDeliveryLogModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: WebhookDeliveryLog) -> WebhookDeliveryLog:
        model = WebhookDeliveryLogModel(
            id=entity.id,
            subscription_id=entity.subscription_id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            event_type=entity.event_type.value,
            event_id=entity.event_id,
            payload=entity.payload,
            status=entity.status.value,
            attempt_count=entity.attempt_count,
            next_attempt_at=entity.next_attempt_at,
            last_attempt_at=entity.last_attempt_at,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: WebhookDeliveryLog) -> WebhookDeliveryLog:
        model = await self.session.get(WebhookDeliveryLogModel, entity.id)
        if not model:
            raise ValueError(f"WebhookDeliveryLog {entity.id} not found")
        model.status = entity.status.value
        model.attempt_count = entity.attempt_count
        model.next_attempt_at = entity.next_attempt_at
        model.last_attempt_at = entity.last_attempt_at
        model.last_status_code = entity.last_status_code
        model.last_response_body = entity.last_response_body
        model.last_error = entity.last_error
        model.exhausted_at = entity.exhausted_at
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        model = await self.session.get(WebhookDeliveryLogModel, entity_id)
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def count(self) -> int:
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count()).select_from(WebhookDeliveryLogModel)
        )
        return result.scalar_one()

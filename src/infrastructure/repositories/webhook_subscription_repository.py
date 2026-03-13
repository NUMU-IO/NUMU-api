"""Webhook subscription repository implementation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.webhook import WebhookEventType, WebhookSubscription
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookSubscriptionRepository,
)
from src.infrastructure.database.models.tenant.webhook import WebhookSubscriptionModel


class WebhookSubscriptionRepository(IWebhookSubscriptionRepository):
    """Webhook subscription repository using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: WebhookSubscriptionModel) -> WebhookSubscription:
        return WebhookSubscription(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            url=model.url,
            events=[WebhookEventType(e) for e in model.events],
            secret=model.secret,
            is_active=model.is_active,
            description=model.description,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> WebhookSubscription | None:
        result = await self.session.get(WebhookSubscriptionModel, entity_id)
        return self._to_entity(result) if result else None

    async def get_all(
        self, skip: int = 0, limit: int = 100
    ) -> list[WebhookSubscription]:
        query = select(WebhookSubscriptionModel).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_store(self, store_id: UUID) -> list[WebhookSubscription]:
        query = (
            select(WebhookSubscriptionModel)
            .where(WebhookSubscriptionModel.store_id == store_id)
            .order_by(WebhookSubscriptionModel.created_at.desc())
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_active_for_event(
        self, store_id: UUID, event_type: WebhookEventType
    ) -> list[WebhookSubscription]:
        """Uses PostgreSQL @> (array contains) operator for efficient lookup."""
        query = select(WebhookSubscriptionModel).where(
            WebhookSubscriptionModel.store_id == store_id,
            WebhookSubscriptionModel.is_active.is_(True),
            WebhookSubscriptionModel.events.contains([event_type.value]),
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_store_and_id(
        self, store_id: UUID, subscription_id: UUID
    ) -> WebhookSubscription | None:
        query = select(WebhookSubscriptionModel).where(
            WebhookSubscriptionModel.id == subscription_id,
            WebhookSubscriptionModel.store_id == store_id,
        )
        result = await self.session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def create(self, entity: WebhookSubscription) -> WebhookSubscription:
        model = WebhookSubscriptionModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            url=entity.url,
            events=[e.value for e in entity.events],
            secret=entity.secret,
            is_active=entity.is_active,
            description=entity.description,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: WebhookSubscription) -> WebhookSubscription:
        model = await self.session.get(WebhookSubscriptionModel, entity.id)
        if not model:
            raise ValueError(f"WebhookSubscription {entity.id} not found")
        model.url = entity.url
        model.events = [e.value for e in entity.events]
        model.is_active = entity.is_active
        model.description = entity.description
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        model = await self.session.get(WebhookSubscriptionModel, entity_id)
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def count(self) -> int:
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count()).select_from(WebhookSubscriptionModel)
        )
        return result.scalar_one()

"""Webhook delivery log repository implementation."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
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

#: How long a claimed delivery stays off the queue while it is attempted.
CLAIM_LEASE = timedelta(minutes=2)


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

    async def claim_pending_retries(
        self, now: datetime, limit: int = 100
    ) -> list[WebhookDeliveryLog]:
        """Take ownership of the deliveries due now — drives the beat poller.

        Locking and leasing together: ``SKIP LOCKED`` keeps two pollers from
        selecting the same row, and pushing ``next_attempt_at`` a lease ahead
        keeps the next tick from re-selecting it while this attempt is still
        in flight. Without both, a slow endpoint received every event twice.
        The lease is short enough that a worker lost mid-attempt only delays
        that delivery, because the row is still PENDING and comes back.
        """
        query = (
            select(WebhookDeliveryLogModel)
            .where(
                WebhookDeliveryLogModel.status == WebhookDeliveryStatus.PENDING,
                WebhookDeliveryLogModel.next_attempt_at <= now,
            )
            .order_by(WebhookDeliveryLogModel.next_attempt_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        models = (await self.session.execute(query)).scalars().all()
        for model in models:
            model.next_attempt_at = now + CLAIM_LEASE
        return [self._to_entity(m) for m in models]

    async def purge_before(self, cutoff: datetime, limit: int = 5000) -> int:
        """Delete settled delivery logs older than ``cutoff``.

        Nothing pruned these, so the table grew for the life of the store.
        Only settled rows go: a PENDING row is still owed an attempt.
        """
        ids = (
            (
                await self.session.execute(
                    select(WebhookDeliveryLogModel.id)
                    .where(
                        WebhookDeliveryLogModel.created_at < cutoff,
                        WebhookDeliveryLogModel.status.in_((
                            WebhookDeliveryStatus.SUCCESS,
                            WebhookDeliveryStatus.FAILED,
                            WebhookDeliveryStatus.EXHAUSTED,
                        )),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await self.session.execute(
                delete(WebhookDeliveryLogModel).where(
                    WebhookDeliveryLogModel.id.in_(ids)
                )
            )
        return len(ids)

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

"""SQLAlchemy implementation of the TikTokEventLog repository.

Sibling of ``MetaEventLogRepository``. Mirrors the conventions of the
other tenant-scoped repositories:

  * All queries apply an explicit ``tenant_id`` filter when a tenant
    context is active (defense-in-depth alongside Postgres RLS).
  * ``create()`` calls ``flush()`` so the caller — and any concurrent
    transaction — observes the UNIQUE constraint immediately. **The
    IntegrityError is intentionally allowed to propagate** so the Celery
    task can use it as its "already sent" dedup signal.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.tiktok_event_log import TikTokEventLog
from src.core.interfaces.repositories.tiktok_event_log_repository import (
    ITikTokEventLogRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.tiktok_event_log import (
    TikTokEventLogModel,
)


class TikTokEventLogRepository(ITikTokEventLogRepository):
    """Async SQLAlchemy repository for ``tiktok_event_log`` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tenant_filter(self, query: Any) -> Any:
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(TikTokEventLogModel.tenant_id == tid)
        return query

    @staticmethod
    def _to_entity(model: TikTokEventLogModel) -> TikTokEventLog:
        return TikTokEventLog(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            event_id=model.event_id,
            event_name=model.event_name,
            event_time=model.event_time,
            pixel_id=model.pixel_id,
            request_payload=model.request_payload,
            response_status=model.response_status,
            response_code=model.response_code,
            response_body=model.response_body,
            request_id=model.request_id,
            attempt_count=model.attempt_count,
            last_error=model.last_error,
            sent_at=model.sent_at,
            created_at=model.created_at,
            # Entity has updated_at from BaseEntity; the row doesn't. Use
            # created_at as a proxy so equality checks don't break.
            updated_at=model.created_at,
        )

    @staticmethod
    def _to_model(entity: TikTokEventLog) -> TikTokEventLogModel:
        return TikTokEventLogModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            store_id=entity.store_id,
            event_id=entity.event_id,
            event_name=entity.event_name,
            event_time=entity.event_time,
            pixel_id=entity.pixel_id,
            request_payload=entity.request_payload,
            response_status=entity.response_status,
            response_code=entity.response_code,
            response_body=entity.response_body,
            request_id=entity.request_id,
            attempt_count=entity.attempt_count,
            last_error=entity.last_error,
            sent_at=entity.sent_at,
        )

    # ------------------------------------------------------------------
    # ITikTokEventLogRepository
    # ------------------------------------------------------------------

    async def create(self, entity: TikTokEventLog) -> TikTokEventLog:
        """Insert a new row.

        IntegrityError on the ``(store_id, event_id)`` UNIQUE constraint
        propagates by design — the Celery task catches it and treats it
        as "already sent, skip the outbound Events API call".
        """
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update_response(
        self,
        log_id: UUID,
        status: int,
        code: int | None,
        body: dict | None,
        request_id: str | None,
        sent_at: datetime,
    ) -> TikTokEventLog | None:
        query = select(TikTokEventLogModel).where(TikTokEventLogModel.id == log_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        model.response_status = status
        model.response_code = code
        model.response_body = body
        model.request_id = request_id
        model.sent_at = sent_at
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update_error(
        self,
        log_id: UUID,
        error: str,
        attempt_count: int,
    ) -> TikTokEventLog | None:
        query = select(TikTokEventLogModel).where(TikTokEventLogModel.id == log_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        model.last_error = error[:500] if error else None
        model.attempt_count = attempt_count
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def recent_for_store(
        self,
        store_id: UUID,
        limit: int = 20,
    ) -> list[TikTokEventLog]:
        query = (
            select(TikTokEventLogModel)
            .where(TikTokEventLogModel.store_id == store_id)
            .order_by(TikTokEventLogModel.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_failed_in_window(
        self,
        store_id: UUID,
        since: datetime,
    ) -> int:
        # "Failed" = HTTP 4xx/5xx, no response recorded, OR a non-zero
        # TikTok business code. Mirrors the partial index
        # `idx_tiktok_event_log_failed`.
        query = select(func.count(TikTokEventLogModel.id)).where(
            and_(
                TikTokEventLogModel.store_id == store_id,
                TikTokEventLogModel.created_at >= since,
                or_(
                    TikTokEventLogModel.response_status.is_(None),
                    TikTokEventLogModel.response_status >= 400,
                    TikTokEventLogModel.response_code != 0,
                ),
            )
        )
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

"""SQLAlchemy implementation of the MetaEventLog repository.

Mirrors the conventions of the other tenant-scoped repositories in
this package:

  * All queries apply an explicit ``tenant_id`` filter when a tenant
    context is active (defense-in-depth alongside Postgres RLS).
  * ``create()`` calls ``flush()`` so the caller — and any concurrent
    transaction — observes the UNIQUE constraint immediately. **The
    IntegrityError is intentionally allowed to propagate** so Phase
    2's Celery task can use it as its "already sent" dedup signal.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.meta_event_log import MetaEventLog
from src.core.interfaces.repositories.meta_event_log_repository import (
    IMetaEventLogRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.meta_event_log import (
    MetaEventLogModel,
)


class MetaEventLogRepository(IMetaEventLogRepository):
    """Async SQLAlchemy repository for ``meta_event_log`` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tenant_filter(self, query: Any) -> Any:
        """Apply tenant_id filter if a tenant context is active.

        Typed as ``Any`` because SQLAlchemy's ``Select[T]`` generic
        doesn't compose cleanly with mypy across the variety of
        select shapes (entity vs scalar count) we pass through here.
        Matches the pattern used in MessageLogRepository.
        """
        tid = get_tenant_id()
        if tid:
            return query.where(MetaEventLogModel.tenant_id == tid)
        return query

    @staticmethod
    def _to_entity(model: MetaEventLogModel) -> MetaEventLog:
        return MetaEventLog(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            event_id=model.event_id,
            event_name=model.event_name,
            event_time=model.event_time,
            pixel_id=model.pixel_id,
            request_payload=model.request_payload,
            response_status=model.response_status,
            response_body=model.response_body,
            fbtrace_id=model.fbtrace_id,
            attempt_count=model.attempt_count,
            last_error=model.last_error,
            sent_at=model.sent_at,
            created_at=model.created_at,
            # Entity has updated_at from BaseEntity; the row doesn't.
            # Use created_at as a proxy so equality checks don't break.
            updated_at=model.created_at,
        )

    @staticmethod
    def _to_model(entity: MetaEventLog) -> MetaEventLogModel:
        return MetaEventLogModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            store_id=entity.store_id,
            event_id=entity.event_id,
            event_name=entity.event_name,
            event_time=entity.event_time,
            pixel_id=entity.pixel_id,
            request_payload=entity.request_payload,
            response_status=entity.response_status,
            response_body=entity.response_body,
            fbtrace_id=entity.fbtrace_id,
            attempt_count=entity.attempt_count,
            last_error=entity.last_error,
            sent_at=entity.sent_at,
        )

    # ------------------------------------------------------------------
    # IMetaEventLogRepository
    # ------------------------------------------------------------------

    async def create(self, entity: MetaEventLog) -> MetaEventLog:
        """Insert a new row.

        IntegrityError on the ``(store_id, event_id)`` UNIQUE constraint
        propagates by design — the Phase 2 Celery task catches it and
        treats it as "already sent, skip the outbound CAPI call".
        """
        model = self._to_model(entity)
        self.session.add(model)
        # flush — not commit — so the constraint check happens now but
        # the surrounding transaction (if any) can still be rolled back.
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update_response(
        self,
        log_id: UUID,
        status: int,
        body: dict | None,
        fbtrace_id: str | None,
        sent_at: datetime,
    ) -> MetaEventLog | None:
        query = select(MetaEventLogModel).where(MetaEventLogModel.id == log_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        model.response_status = status
        model.response_body = body
        model.fbtrace_id = fbtrace_id
        model.sent_at = sent_at
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update_error(
        self,
        log_id: UUID,
        error: str,
        attempt_count: int,
    ) -> MetaEventLog | None:
        query = select(MetaEventLogModel).where(MetaEventLogModel.id == log_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        # Truncate to match the entity's bound — keeps the column
        # bounded even if a transport returns a giant traceback.
        model.last_error = error[:500] if error else None
        model.attempt_count = attempt_count
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def recent_for_store(
        self,
        store_id: UUID,
        limit: int = 20,
    ) -> list[MetaEventLog]:
        query = (
            select(MetaEventLogModel)
            .where(MetaEventLogModel.store_id == store_id)
            .order_by(MetaEventLogModel.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_failed_in_window(
        self,
        store_id: UUID,
        since: datetime,
    ) -> int:
        # "Failed" = either Meta returned 4xx/5xx OR we never recorded a
        # response (network error, worker crash, etc.). Mirrors the
        # partial index `idx_meta_event_log_failed`, so this query
        # benefits from it on hot stores.
        query = select(func.count(MetaEventLogModel.id)).where(
            and_(
                MetaEventLogModel.store_id == store_id,
                MetaEventLogModel.created_at >= since,
                or_(
                    MetaEventLogModel.response_status.is_(None),
                    MetaEventLogModel.response_status >= 400,
                ),
            )
        )
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

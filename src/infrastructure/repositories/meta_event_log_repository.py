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

# Local aliases for the statuses this module compares against. Imported by
# value rather than referenced through the enum on every line — these appear
# inside SQL expressions where a StrEnum member reads as noise.
_PENDING = "pending"
_RETRYING = "retrying"
_EXPIRED = "expired"
_OPEN = (_PENDING, _RETRYING)


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
            status=model.status,
            next_retry_at=model.next_retry_at,
            expires_at=model.expires_at,
            priority=model.priority,
            failure_kind=model.failure_kind,
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
            status=entity.status,
            next_retry_at=entity.next_retry_at,
            expires_at=entity.expires_at,
            priority=entity.priority,
            failure_kind=entity.failure_kind,
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
        *,
        delivery_status: str | None = None,
        failure_kind: str | None = None,
        next_retry_at: datetime | None = None,
        attempt_count: int | None = None,
    ) -> MetaEventLog | None:
        """Record Meta's answer, and where that leaves the delivery.

        The keyword-only arguments are the outbox half. They are optional so
        the pre-outbox call shape still works — but a caller that omits
        ``delivery_status`` leaves the row ``pending``, which the reclaim
        sweep will eventually pick up. Always pass it.

        ``attempt_count`` matters more than it looks. Celery's in-broker
        retries do not touch the row, so without it the column still reads 1
        when Celery hands the event to the sweep — and the sweep indexes its
        backoff ladder off that column. The ladder would restart at its first
        rung on every pass and the event would retry every five minutes until
        it expired, which is precisely the unbounded loop the ladder exists
        to prevent.
        """
        query = select(MetaEventLogModel).where(MetaEventLogModel.id == log_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        model.response_status = status
        model.response_body = body
        model.fbtrace_id = fbtrace_id
        model.sent_at = sent_at
        if attempt_count is not None:
            model.attempt_count = attempt_count
        if delivery_status is not None:
            model.status = delivery_status
        model.failure_kind = failure_kind
        # Cleared on a terminal outcome so a settled row can never look due.
        model.next_retry_at = next_retry_at
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update_error(
        self,
        log_id: UUID,
        error: str,
        attempt_count: int,
        *,
        delivery_status: str | None = None,
        failure_kind: str | None = None,
        next_retry_at: datetime | None = None,
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
        if delivery_status is not None:
            model.status = delivery_status
        if failure_kind is not None:
            model.failure_kind = failure_kind
        if next_retry_at is not None:
            model.next_retry_at = next_retry_at
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

    # ------------------------------------------------------------------
    # Outbox mechanics
    # ------------------------------------------------------------------
    #
    # These three run for the delivery sweep, which is deliberately
    # CROSS-TENANT: it scans every store's owed events in one pass, the
    # same way the orphan sweep does. `_tenant_filter` is therefore NOT
    # applied here and the caller must hold an RLS bypass.
    #
    # Isolation is preserved by what happens next, not by the scan: every
    # claimed row carries its own tenant_id and store_id, and the send task
    # re-narrows to that tenant before it touches anything.

    async def expire_overdue(self, *, now: datetime, limit: int = 5_000) -> int:
        """Retire rows that may no longer be sent. Returns the count.

        Past ``expires_at`` a resend is no longer merged by Meta — it lands
        as a second conversion and inflates the merchant's revenue. So this
        is not cleanup: leaving these rows claimable would actively corrupt
        reporting.
        """
        from sqlalchemy import update

        doomed = (
            select(MetaEventLogModel.id)
            .where(
                MetaEventLogModel.status.in_(_OPEN),
                MetaEventLogModel.expires_at.isnot(None),
                MetaEventLogModel.expires_at <= now,
            )
            .limit(limit)
        )
        result = await self.session.execute(
            update(MetaEventLogModel)
            .where(MetaEventLogModel.id.in_(doomed))
            .values(status=_EXPIRED, next_retry_at=None)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def claim_due(
        self,
        *,
        now: datetime,
        lease_until: datetime,
        limit: int = 200,
    ) -> list[MetaEventLog]:
        """Take ownership of up to ``limit`` events whose next attempt is due.

        The claim is the lease: ``next_retry_at`` is pushed to
        ``lease_until`` in the same statement that selects the row, so a
        worker that dies before settling simply lets the lease lapse and the
        row becomes claimable again. No heartbeat, no reaper.

        ``FOR UPDATE SKIP LOCKED`` is what makes concurrent sweeps safe:
        two workers running at once take disjoint sets rather than blocking
        or double-claiming.

        Ordered by ``priority`` first, so a Purchase backlog drains before a
        PageView backlog even when both are overdue.

        Delivery is at-least-once by construction — a crash between the send
        and the settle re-delivers. That is SAFE here and only here: every
        claimable row is inside Meta's 48h dedup window (``expire_overdue``
        guarantees it), so a re-delivery is merged rather than counted twice.

        Read only the columns this statement does NOT write. `synchronize_session
        =False` leaves the session's own copies untouched, so if the caller had
        already loaded a row, the identity map hands back its pre-claim
        ``status`` and ``attempt_count`` rather than the RETURNING values. The
        sweep reads id / store_id / pixel_id / priority / payload, none of which
        the claim changes; anything needing the post-claim count must re-read.
        """
        from sqlalchemy import update

        due = (
            select(MetaEventLogModel.id)
            .where(
                MetaEventLogModel.status.in_(_OPEN),
                MetaEventLogModel.next_retry_at.isnot(None),
                MetaEventLogModel.next_retry_at <= now,
            )
            .order_by(
                MetaEventLogModel.priority.asc(),
                MetaEventLogModel.next_retry_at.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        claimed = await self.session.execute(
            update(MetaEventLogModel)
            .where(MetaEventLogModel.id.in_(due))
            .values(
                status=_PENDING,
                next_retry_at=lease_until,
                attempt_count=MetaEventLogModel.attempt_count + 1,
            )
            .returning(MetaEventLogModel)
            .execution_options(synchronize_session=False)
        )
        return [self._to_entity(m) for m in claimed.scalars().all()]

    async def delivery_counts(
        self,
        *,
        since: datetime,
        store_id: UUID | None = None,
    ) -> dict[str, int]:
        """``{status: count}`` over rows created since ``since``.

        Answers "how many are pending / retrying / dead-lettered / expired"
        in one query. Scoped to a store when given; otherwise fleet-wide for
        the admin overview — which is why ``_tenant_filter`` is applied only
        in the per-store case.
        """
        query = (
            select(MetaEventLogModel.status, func.count(MetaEventLogModel.id))
            .where(MetaEventLogModel.created_at >= since)
            .group_by(MetaEventLogModel.status)
        )
        if store_id is not None:
            query = self._tenant_filter(
                query.where(MetaEventLogModel.store_id == store_id)
            )
        rows = (await self.session.execute(query)).all()
        return {str(status): int(count) for status, count in rows}

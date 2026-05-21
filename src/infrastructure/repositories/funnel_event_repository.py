"""Funnel event repository for conversion funnel tracking."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.funnel_event import FunnelEventModel


class FunnelEventRepository:
    """Repository for funnel events (append-only)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(FunnelEventModel.tenant_id == tid)
        return query

    async def create(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        step: str,
        session_fingerprint: str | None = None,
        customer_id: UUID | None = None,
        step_data: dict | None = None,
        event_id: UUID | None = None,
        # Feature 001 — attribution columns. All optional; null for
        # visitors who never landed via a campaign-tagged URL.
        utm_source: str | None = None,
        utm_medium: str | None = None,
        utm_campaign: str | None = None,
        utm_term: str | None = None,
        utm_content: str | None = None,
        campaign_id: UUID | None = None,
        referrer: str | None = None,
    ) -> None:
        """Record a funnel event.

        ``event_id`` is the optional client-provided idempotency key.
        Persisted as-is; uniqueness is enforced by the partial UNIQUE
        index ``ux_funnel_events_event_id`` (Step 09 migration).
        """
        event = FunnelEventModel(
            id=uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            step=step,
            session_fingerprint=session_fingerprint,
            customer_id=customer_id,
            step_data=step_data,
            event_id=event_id,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_term=utm_term,
            utm_content=utm_content,
            campaign_id=campaign_id,
            referrer=referrer,
        )
        self.session.add(event)
        await self.session.flush()

    async def get_funnel_counts(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, int]:
        """Get unique session counts per funnel step."""
        query = (
            select(
                FunnelEventModel.step,
                func.count(func.distinct(FunnelEventModel.session_fingerprint)).label(
                    "count"
                ),
            )
            .where(FunnelEventModel.store_id == store_id)
            .where(FunnelEventModel.created_at >= date_from)
            .where(FunnelEventModel.created_at <= date_to)
            .group_by(FunnelEventModel.step)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return {row.step: row.count for row in result.all()}

    async def get_daily_funnel_counts(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
        steps: list[str] | None = None,
    ) -> list[dict]:
        """Get daily unique session counts per funnel step."""
        query = (
            select(
                cast(FunnelEventModel.created_at, Date).label("day"),
                FunnelEventModel.step,
                func.count(func.distinct(FunnelEventModel.session_fingerprint)).label(
                    "count"
                ),
            )
            .where(FunnelEventModel.store_id == store_id)
            .where(FunnelEventModel.created_at >= date_from)
            .where(FunnelEventModel.created_at <= date_to)
            .group_by(cast(FunnelEventModel.created_at, Date), FunnelEventModel.step)
            .order_by(cast(FunnelEventModel.created_at, Date))
        )
        if steps:
            query = query.where(FunnelEventModel.step.in_(steps))
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return [
            {"day": str(row.day), "step": row.step, "count": row.count}
            for row in result.all()
        ]

    async def get_step_pair_avg_minutes(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
        step_from: str,
        step_to: str,
    ) -> float | None:
        """Get average minutes between two funnel steps for the same session.

        ``session_fingerprint`` is generated client-side and only
        guaranteed unique within a single browser/storefront — across
        stores, two different visitors could theoretically share a
        fingerprint. The join therefore matches on ``(fingerprint,
        store_id)`` so step pairings can never cross tenant.
        """

        def _per_store_min(step: str, alias: str):
            return (
                select(
                    FunnelEventModel.session_fingerprint.label("fp"),
                    FunnelEventModel.store_id.label("store_id"),
                    func.min(FunnelEventModel.created_at).label("ts"),
                )
                .where(FunnelEventModel.store_id == store_id)
                .where(FunnelEventModel.step == step)
                .where(FunnelEventModel.created_at >= date_from)
                .where(FunnelEventModel.created_at <= date_to)
                .group_by(
                    FunnelEventModel.session_fingerprint,
                    FunnelEventModel.store_id,
                )
            ).subquery(alias)

        from_sub = _per_store_min(step_from, "from_step")
        to_sub = _per_store_min(step_to, "to_step")

        query = select(
            func.avg(func.extract("epoch", to_sub.c.ts - from_sub.c.ts) / 60).label(
                "avg_minutes"
            )
        ).select_from(
            from_sub.join(
                to_sub,
                (from_sub.c.fp == to_sub.c.fp)
                & (from_sub.c.store_id == to_sub.c.store_id),
            )
        )

        result = await self.session.execute(query)
        row = result.one()
        return float(row.avg_minutes) if row.avg_minutes is not None else None

    async def get_steps_per_session(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> dict[str, set[str]]:
        """``{session_fingerprint: {step1, step2, ...}}`` in a single query.

        Replaces the loop that called ``get_sessions_with_step`` once
        per funnel step (5–6 round-trips). Each fingerprint maps to the
        set of distinct steps it triggered in the window so the route
        can compute "deepest step" or "session has step X" with O(1)
        lookups.
        """
        query = (
            select(
                FunnelEventModel.session_fingerprint,
                FunnelEventModel.step,
            )
            .where(FunnelEventModel.store_id == store_id)
            .where(FunnelEventModel.created_at >= date_from)
            .where(FunnelEventModel.created_at <= date_to)
            .where(FunnelEventModel.session_fingerprint.isnot(None))
            .group_by(FunnelEventModel.session_fingerprint, FunnelEventModel.step)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        out: dict[str, set[str]] = {}
        for row in result.all():
            out.setdefault(row.session_fingerprint, set()).add(row.step)
        return out

    async def get_attribution_data(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict]:
        """Get marketing attribution from page_view events with UTM data."""
        # Get page_view events that have utm_source in step_data
        query = (
            select(FunnelEventModel)
            .where(FunnelEventModel.store_id == store_id)
            .where(FunnelEventModel.step == "page_view")
            .where(FunnelEventModel.created_at >= date_from)
            .where(FunnelEventModel.created_at <= date_to)
            .where(FunnelEventModel.step_data.isnot(None))
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return [
            {
                "session_fingerprint": e.session_fingerprint,
                "step_data": e.step_data,
            }
            for e in result.scalars().all()
        ]

    async def get_session_events(
        self,
        store_id: UUID,
        session_fingerprint: str,
    ) -> list[dict]:
        """Get all funnel events for a specific session, ordered by time."""
        query = (
            select(FunnelEventModel)
            .where(FunnelEventModel.store_id == store_id)
            .where(FunnelEventModel.session_fingerprint == session_fingerprint)
            .order_by(FunnelEventModel.created_at)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return [
            {
                "step": e.step,
                "step_data": e.step_data,
                "customer_id": str(e.customer_id) if e.customer_id else None,
                "created_at": e.created_at,
            }
            for e in result.scalars().all()
        ]

    async def get_sessions_with_step(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
        step: str,
    ) -> set[str]:
        """Get session fingerprints that reached a specific funnel step."""
        query = (
            select(func.distinct(FunnelEventModel.session_fingerprint))
            .where(FunnelEventModel.store_id == store_id)
            .where(FunnelEventModel.step == step)
            .where(FunnelEventModel.created_at >= date_from)
            .where(FunnelEventModel.created_at <= date_to)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return {row[0] for row in result.all() if row[0]}

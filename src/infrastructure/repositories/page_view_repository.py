"""Page view repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models import PageViewModel


class PageViewRepository:
    """Page view repository implementation using SQLAlchemy.

    All queries include an explicit tenant_id filter as a defense-in-depth
    measure alongside PostgreSQL RLS policies.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(PageViewModel.tenant_id == tid)
        return query

    async def create(
        self,
        store_id: UUID,
        tenant_id: UUID,
        path: str,
        session_fingerprint: str | None,
        ip_address: str | None,
        user_agent: str | None,
        referrer: str | None,
    ) -> None:
        """Insert a new page view record."""
        page_view = PageViewModel(
            store_id=store_id,
            tenant_id=tenant_id,
            path=path,
            session_fingerprint=session_fingerprint,
            ip_address=ip_address,
            user_agent=user_agent,
            referrer=referrer,
        )
        self.session.add(page_view)
        await self.session.flush()

    async def get_daily_visits(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> list[tuple[str, int]]:
        """Get daily visit counts grouped by date."""
        date_col = func.date(PageViewModel.created_at).label("visit_date")
        query = (
            select(date_col, func.count().label("visit_count"))
            .where(PageViewModel.store_id == store_id)
            .where(PageViewModel.created_at >= date_from)
            .where(PageViewModel.created_at <= date_to)
            .group_by(date_col)
            .order_by(date_col)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return [(str(row.visit_date), row.visit_count) for row in result.all()]

    async def count_unique_visitors(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
    ) -> int:
        """Count unique visitors by distinct session fingerprint."""
        query = (
            select(
                func.count(func.distinct(PageViewModel.session_fingerprint)).label(
                    "unique_count"
                )
            )
            .where(PageViewModel.store_id == store_id)
            .where(PageViewModel.created_at >= date_from)
            .where(PageViewModel.created_at <= date_to)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return result.scalar_one()

    async def get_sessions_summary(
        self,
        store_id: UUID,
        date_from: datetime,
        date_to: datetime,
        limit: int = 100,
    ) -> list[dict]:
        """Get session summaries grouped by fingerprint."""
        query = (
            select(
                PageViewModel.session_fingerprint,
                func.count().label("page_count"),
                func.min(PageViewModel.created_at).label("started_at"),
                func.max(PageViewModel.created_at).label("ended_at"),
                func.min(PageViewModel.user_agent).label("user_agent"),
                func.min(PageViewModel.referrer).label("referrer"),
            )
            .where(PageViewModel.store_id == store_id)
            .where(PageViewModel.created_at >= date_from)
            .where(PageViewModel.created_at <= date_to)
            .where(PageViewModel.session_fingerprint.isnot(None))
            .group_by(PageViewModel.session_fingerprint)
            .order_by(func.max(PageViewModel.created_at).desc())
            .limit(limit)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return [
            {
                "session_fingerprint": row.session_fingerprint,
                "page_count": row.page_count,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "user_agent": row.user_agent,
                "referrer": row.referrer,
            }
            for row in result.all()
        ]

    async def get_session_pages(
        self,
        store_id: UUID,
        session_fingerprint: str,
    ) -> list[dict]:
        """Get all page views for a specific session, ordered by time."""
        query = (
            select(
                PageViewModel.path,
                PageViewModel.created_at,
                PageViewModel.referrer,
                PageViewModel.user_agent,
            )
            .where(PageViewModel.store_id == store_id)
            .where(PageViewModel.session_fingerprint == session_fingerprint)
            .order_by(PageViewModel.created_at)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return [
            {
                "path": row.path,
                "created_at": row.created_at,
                "referrer": row.referrer,
                "user_agent": row.user_agent,
            }
            for row in result.all()
        ]

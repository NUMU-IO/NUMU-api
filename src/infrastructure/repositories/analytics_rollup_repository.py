"""Analytics daily rollup repository implementation."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.analytics_rollup import (
    AnalyticsDailyRollupModel,
)


class AnalyticsRollupRepository:
    """Repository for analytics daily rollup data.

    All queries include an explicit tenant_id filter as a defense-in-depth
    measure alongside PostgreSQL RLS policies.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(AnalyticsDailyRollupModel.tenant_id == tid)
        return query

    async def get_range(
        self,
        store_id: UUID,
        date_from: date,
        date_to: date,
    ) -> Sequence[AnalyticsDailyRollupModel]:
        """Get rollup rows for a store within a date range."""
        query = (
            select(AnalyticsDailyRollupModel)
            .where(AnalyticsDailyRollupModel.store_id == store_id)
            .where(AnalyticsDailyRollupModel.rollup_date >= date_from)
            .where(AnalyticsDailyRollupModel.rollup_date <= date_to)
            .order_by(AnalyticsDailyRollupModel.rollup_date)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_aggregated(
        self,
        store_id: UUID,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Get aggregated sums across a date range for a store."""
        query = (
            select(
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.total_revenue_cents), 0
                ).label("total_revenue_cents"),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.total_orders), 0
                ).label("total_orders"),
                func.coalesce(func.sum(AnalyticsDailyRollupModel.paid_orders), 0).label(
                    "paid_orders"
                ),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.cancelled_orders), 0
                ).label("cancelled_orders"),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.new_customers), 0
                ).label("new_customers"),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.returning_customers), 0
                ).label("returning_customers"),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.total_page_views), 0
                ).label("total_page_views"),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.unique_visitors), 0
                ).label("unique_visitors"),
                func.coalesce(func.sum(AnalyticsDailyRollupModel.cod_orders), 0).label(
                    "cod_orders"
                ),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.cod_delivered), 0
                ).label("cod_delivered"),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.cod_rejected), 0
                ).label("cod_rejected"),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.refund_count), 0
                ).label("refund_count"),
                func.coalesce(
                    func.sum(AnalyticsDailyRollupModel.refund_amount_cents), 0
                ).label("refund_amount_cents"),
            )
            .where(AnalyticsDailyRollupModel.store_id == store_id)
            .where(AnalyticsDailyRollupModel.rollup_date >= date_from)
            .where(AnalyticsDailyRollupModel.rollup_date <= date_to)
        )
        query = self._tenant_filter(query)
        result = await self.session.execute(query)
        row = result.one()
        return {
            "total_revenue_cents": row.total_revenue_cents,
            "total_orders": row.total_orders,
            "paid_orders": row.paid_orders,
            "cancelled_orders": row.cancelled_orders,
            "new_customers": row.new_customers,
            "returning_customers": row.returning_customers,
            "total_page_views": row.total_page_views,
            "unique_visitors": row.unique_visitors,
            "cod_orders": row.cod_orders,
            "cod_delivered": row.cod_delivered,
            "cod_rejected": row.cod_rejected,
            "refund_count": row.refund_count,
            "refund_amount_cents": row.refund_amount_cents,
        }

    async def upsert(
        self,
        tenant_id: UUID,
        store_id: UUID,
        rollup_date: date,
        data: dict,
    ) -> None:
        """Insert or update a rollup row (idempotent)."""
        values = {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "store_id": store_id,
            "rollup_date": rollup_date,
            **data,
        }

        stmt = pg_insert(AnalyticsDailyRollupModel).values(**values)

        # On conflict with (store_id, rollup_date) → update all metric columns
        update_cols = {
            k: v
            for k, v in values.items()
            if k not in ("id", "tenant_id", "store_id", "rollup_date", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                AnalyticsDailyRollupModel.store_id,
                AnalyticsDailyRollupModel.rollup_date,
            ],
            set_=update_cols,
        )

        await self.session.execute(stmt)

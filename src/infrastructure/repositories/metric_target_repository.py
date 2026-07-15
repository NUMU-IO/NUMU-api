"""Metric-target repository — merchant goals per (store, metric, period)."""

from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models import MetricTargetModel


class MetricTargetRepository:
    """CRUD for metric targets. Explicit tenant filters (defense in depth)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(MetricTargetModel.tenant_id == tid)
        return query

    async def get_for_store(self, store_id: UUID) -> list[MetricTargetModel]:
        query = select(MetricTargetModel).where(MetricTargetModel.store_id == store_id)
        result = await self.session.execute(self._tenant_filter(query))
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        metric: str,
        period: str,
        target_value: int,
    ) -> None:
        """Insert or update one goal (idempotent on the unique key)."""
        stmt = pg_insert(MetricTargetModel).values(
            id=uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            metric=metric,
            period=period,
            target_value=target_value,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_metric_targets_store_metric_period",
            set_={"target_value": target_value},
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def remove(self, store_id: UUID, metric: str, period: str) -> None:
        """Delete a goal — a PUT with value 0 means "stop tracking this"."""
        query = delete(MetricTargetModel).where(
            MetricTargetModel.store_id == store_id,
            MetricTargetModel.metric == metric,
            MetricTargetModel.period == period,
        )
        tid = get_tenant_id()
        if tid:
            query = query.where(MetricTargetModel.tenant_id == tid)
        await self.session.execute(query)
        await self.session.flush()

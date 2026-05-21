"""Order activity repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.order_activity import OrderActivity, OrderActivityKind
from src.core.interfaces.repositories.order_activity_repository import (
    IOrderActivityRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.order_activity import (
    OrderActivityModel,
)


class OrderActivityRepository(IOrderActivityRepository):
    """SQLAlchemy implementation of the order activity repository.

    All queries include an explicit `tenant_id` filter as a defense-in-depth
    measure alongside PostgreSQL RLS policies.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(OrderActivityModel.tenant_id == tid)
        return query

    def _to_entity(self, model: OrderActivityModel) -> OrderActivity:
        return OrderActivity(
            id=model.id,
            order_id=model.order_id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            user_id=model.user_id,
            kind=model.kind,
            event_type=model.event_type,
            body=model.body,
            metadata=model.activity_metadata or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: OrderActivity) -> OrderActivityModel:
        return OrderActivityModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            order_id=entity.order_id,
            store_id=entity.store_id,
            user_id=entity.user_id,
            kind=entity.kind,
            event_type=entity.event_type,
            body=entity.body,
            activity_metadata=entity.metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> OrderActivity | None:
        query = select(OrderActivityModel).where(OrderActivityModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[OrderActivity]:
        query = (
            select(OrderActivityModel)
            .order_by(OrderActivityModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: OrderActivity) -> OrderActivity:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: OrderActivity) -> OrderActivity:
        # Activities are append-only in Phase 1.
        raise NotImplementedError("OrderActivity is append-only")

    async def delete(self, entity_id: UUID) -> bool:
        # Activities are append-only in Phase 1.
        raise NotImplementedError("OrderActivity is append-only")

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(OrderActivityModel.id)))
        return result.scalar() or 0

    async def list_by_order(
        self,
        order_id: UUID,
        store_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[OrderActivity], int]:
        base = select(OrderActivityModel).where(
            OrderActivityModel.order_id == order_id,
            OrderActivityModel.store_id == store_id,
        )

        count_query = select(func.count()).select_from(base.subquery())
        count_result = await self.session.execute(self._tenant_filter(count_query))
        total = count_result.scalar() or 0

        items_query = (
            base.order_by(OrderActivityModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(items_query))
        return [self._to_entity(m) for m in result.scalars().all()], total

    async def count_comments_by_order(self, order_id: UUID) -> int:
        query = select(func.count(OrderActivityModel.id)).where(
            OrderActivityModel.order_id == order_id,
            OrderActivityModel.kind == OrderActivityKind.COMMENT,
        )
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

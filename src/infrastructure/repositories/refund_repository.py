"""Refund repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.refund import Refund, RefundStatus
from src.core.interfaces.repositories.refund_repository import IRefundRepository
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.refund import RefundModel


class RefundRepository(IRefundRepository):
    """Refund repository implementation using SQLAlchemy.

    All queries include an explicit tenant_id filter as a defense-in-depth
    measure alongside PostgreSQL RLS policies.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(RefundModel.tenant_id == tid)
        return query

    def _to_entity(self, model: RefundModel) -> Refund:
        """Convert database model to domain entity."""
        return Refund(
            id=model.id,
            order_id=model.order_id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            refund_number=model.refund_number,
            refund_type=model.refund_type,
            status=model.status,
            reason=model.reason,
            reason_note=model.reason_note,
            amount=model.amount,
            currency=model.currency,
            payment_provider=model.payment_provider,
            payment_id=model.payment_id,
            provider_refund_id=model.provider_refund_id,
            requested_by=model.requested_by,
            approved_by=model.approved_by,
            rejected_by=model.rejected_by,
            processed_at=model.processed_at,
            completed_at=model.completed_at,
            rejected_at=model.rejected_at,
            failure_reason=model.failure_reason,
            metadata=model.refund_metadata or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Refund) -> RefundModel:
        """Convert domain entity to database model."""
        return RefundModel(
            id=entity.id,
            order_id=entity.order_id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            refund_number=entity.refund_number,
            refund_type=entity.refund_type,
            status=entity.status,
            reason=entity.reason,
            reason_note=entity.reason_note,
            amount=entity.amount,
            currency=entity.currency,
            payment_provider=entity.payment_provider,
            payment_id=entity.payment_id,
            provider_refund_id=entity.provider_refund_id,
            requested_by=entity.requested_by,
            approved_by=entity.approved_by,
            rejected_by=entity.rejected_by,
            processed_at=entity.processed_at,
            completed_at=entity.completed_at,
            rejected_at=entity.rejected_at,
            failure_reason=entity.failure_reason,
            refund_metadata=entity.metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Refund | None:
        """Get refund by ID."""
        query = select(RefundModel).where(RefundModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Refund]:
        """Get all refunds with pagination."""
        query = (
            select(RefundModel)
            .order_by(RefundModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: Refund) -> Refund:
        """Create a new refund."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Refund) -> Refund:
        """Update an existing refund."""
        query = select(RefundModel).where(RefundModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            model.status = entity.status
            model.reason = entity.reason
            model.reason_note = entity.reason_note
            model.amount = entity.amount
            model.provider_refund_id = entity.provider_refund_id
            model.approved_by = entity.approved_by
            model.rejected_by = entity.rejected_by
            model.processed_at = entity.processed_at
            model.completed_at = entity.completed_at
            model.rejected_at = entity.rejected_at
            model.failure_reason = entity.failure_reason
            model.refund_metadata = entity.metadata
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Refund with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a refund by ID."""
        query = select(RefundModel).where(RefundModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of refunds."""
        result = await self.session.execute(select(func.count(RefundModel.id)))
        return result.scalar() or 0

    async def get_by_order(
        self,
        order_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Refund]:
        """Get all refunds for an order."""
        query = (
            select(RefundModel)
            .where(RefundModel.order_id == order_id)
            .order_by(RefundModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_by_store(
        self,
        store_id: UUID,
        status: RefundStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Refund]:
        """Get all refunds for a store."""
        query = select(RefundModel).where(RefundModel.store_id == store_id)
        if status:
            query = query.where(RefundModel.status == status)
        query = query.order_by(RefundModel.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_by_store(
        self,
        store_id: UUID,
        status: RefundStatus | None = None,
    ) -> int:
        """Get total count of refunds for a store."""
        query = select(func.count(RefundModel.id)).where(
            RefundModel.store_id == store_id
        )
        if status:
            query = query.where(RefundModel.status == status)
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

    async def count_by_order(self, order_id: UUID) -> int:
        """Get total count of refunds for an order."""
        query = select(func.count(RefundModel.id)).where(
            RefundModel.order_id == order_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

    async def get_total_refunded_for_order(self, order_id: UUID) -> int:
        """Get total refunded amount (completed refunds) for an order in cents."""
        query = select(func.coalesce(func.sum(RefundModel.amount), 0)).where(
            RefundModel.order_id == order_id,
            RefundModel.status == RefundStatus.COMPLETED,
        )
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

    async def get_next_refund_number(self, store_id: UUID) -> str:
        """Generate next refund number for a store."""
        query = select(func.count(RefundModel.id)).where(
            RefundModel.store_id == store_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        count = result.scalar() or 0
        return f"RFD-{count + 1:04d}"

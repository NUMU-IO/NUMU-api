"""Coupon repository implementation."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.coupon import Coupon, DiscountType
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.infrastructure.database.models import CouponModel, OrderModel


class CouponRepository(ICouponRepository):
    """Coupon repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: CouponModel) -> Coupon:
        """Convert database model to domain entity."""
        return Coupon(
            id=model.id,
            store_id=model.store_id,
            code=model.code,
            description=model.description,
            discount_type=model.discount_type,
            discount_value=model.discount_value,
            min_order_amount=model.min_order_amount,
            max_discount_amount=model.max_discount_amount,
            max_uses=model.max_uses,
            max_uses_per_customer=model.max_uses_per_customer,
            current_usage_count=model.current_usage_count,
            valid_from=model.valid_from,
            valid_to=model.valid_to,
            is_active=model.is_active,
            metadata=model.extra_data or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Coupon) -> CouponModel:
        """Convert domain entity to database model."""
        return CouponModel(
            id=entity.id,
            store_id=entity.store_id,
            code=entity.code,
            description=entity.description,
            discount_type=entity.discount_type,
            discount_value=entity.discount_value,
            min_order_amount=entity.min_order_amount,
            max_discount_amount=entity.max_discount_amount,
            max_uses=entity.max_uses,
            max_uses_per_customer=entity.max_uses_per_customer,
            current_usage_count=entity.current_usage_count,
            valid_from=entity.valid_from,
            valid_to=entity.valid_to,
            is_active=entity.is_active,
            extra_data=entity.metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Coupon | None:
        """Get coupon by ID."""
        result = await self.session.execute(
            select(CouponModel).where(CouponModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Coupon]:
        """Get all coupons with pagination."""
        result = await self.session.execute(
            select(CouponModel).offset(skip).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: Coupon) -> Coupon:
        """Create a new coupon."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Coupon) -> Coupon:
        """Update an existing coupon."""
        result = await self.session.execute(
            select(CouponModel).where(CouponModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.code = entity.code
            model.description = entity.description
            model.discount_type = entity.discount_type
            model.discount_value = entity.discount_value
            model.min_order_amount = entity.min_order_amount
            model.max_discount_amount = entity.max_discount_amount
            model.max_uses = entity.max_uses
            model.max_uses_per_customer = entity.max_uses_per_customer
            model.current_usage_count = entity.current_usage_count
            model.valid_from = entity.valid_from
            model.valid_to = entity.valid_to
            model.is_active = entity.is_active
            model.extra_data = entity.metadata
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Coupon with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a coupon by ID."""
        result = await self.session.execute(
            select(CouponModel).where(CouponModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of coupons."""
        result = await self.session.execute(
            select(func.count(CouponModel.id))
        )
        return result.scalar() or 0

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        is_active: bool | None = None,
    ) -> list[Coupon]:
        """Get all coupons for a store."""
        query = select(CouponModel).where(CouponModel.store_id == store_id)
        if is_active is not None:
            query = query.where(CouponModel.is_active == is_active)
        query = query.order_by(CouponModel.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_by_code(self, store_id: UUID, code: str) -> Coupon | None:
        """Get coupon by code within a store."""
        result = await self.session.execute(
            select(CouponModel).where(
                CouponModel.store_id == store_id,
                CouponModel.code == code.upper(),
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def count_by_store(self, store_id: UUID) -> int:
        """Get total count of coupons for a store."""
        result = await self.session.execute(
            select(func.count(CouponModel.id)).where(CouponModel.store_id == store_id)
        )
        return result.scalar() or 0

    async def increment_usage(self, coupon_id: UUID) -> None:
        """Atomically increment the coupon usage count."""
        await self.session.execute(
            update(CouponModel)
            .where(CouponModel.id == coupon_id)
            .values(current_usage_count=CouponModel.current_usage_count + 1)
        )
        await self.session.flush()

    async def get_customer_usage_count(self, coupon_id: UUID, customer_id: UUID) -> int:
        """Get how many times a customer has used a specific coupon."""
        result = await self.session.execute(
            select(func.count(OrderModel.id)).where(
                OrderModel.coupon_id == coupon_id,
                OrderModel.customer_id == customer_id,
            )
        )
        return result.scalar() or 0

"""Coupon repository implementation."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.coupon import Coupon
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.infrastructure.database.models.tenant.coupon import CouponModel


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
            coupon_type=model.coupon_type,
            value=model.value,
            min_order_amount=model.min_order_amount,
            max_discount_amount=model.max_discount_amount,
            usage_limit=model.usage_limit,
            usage_count=model.usage_count,
            valid_from=model.valid_from,
            valid_until=model.valid_until,
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Coupon) -> CouponModel:
        """Convert domain entity to database model."""
        return CouponModel(
            id=entity.id,
            store_id=entity.store_id,
            code=entity.code,
            coupon_type=entity.coupon_type,
            value=entity.value,
            min_order_amount=entity.min_order_amount,
            max_discount_amount=entity.max_discount_amount,
            usage_limit=entity.usage_limit,
            usage_count=entity.usage_count,
            valid_from=entity.valid_from,
            valid_until=entity.valid_until,
            is_active=entity.is_active,
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

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Coupon]:
        """Get all coupons with pagination."""
        result = await self.session.execute(
            select(CouponModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

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
            model.coupon_type = entity.coupon_type
            model.value = entity.value
            model.min_order_amount = entity.min_order_amount
            model.max_discount_amount = entity.max_discount_amount
            model.usage_limit = entity.usage_limit
            model.usage_count = entity.usage_count
            model.valid_from = entity.valid_from
            model.valid_until = entity.valid_until
            model.is_active = entity.is_active
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
        result = await self.session.execute(select(func.count(CouponModel.id)))
        return result.scalar() or 0

    async def get_by_code(self, store_id: UUID, code: str) -> Coupon | None:
        """Get coupon by code within a store."""
        result = await self.session.execute(
            select(CouponModel).where(
                CouponModel.store_id == store_id,
                CouponModel.code == code.strip().upper(),
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

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
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_by_store(self, store_id: UUID) -> int:
        """Get total count of coupons for a store."""
        result = await self.session.execute(
            select(func.count(CouponModel.id)).where(CouponModel.store_id == store_id)
        )
        return result.scalar() or 0

    async def get_active_by_store(
        self,
        store_id: UUID,
        now: datetime | None = None,
    ) -> list[Coupon]:
        """Get currently active and valid coupons for a store."""
        now = now or datetime.now(UTC)
        query = (
            select(CouponModel)
            .where(
                CouponModel.store_id == store_id,
                CouponModel.is_active.is_(True),
            )
            .where(or_(CouponModel.valid_from.is_(None), CouponModel.valid_from <= now))
            .where(
                or_(CouponModel.valid_until.is_(None), CouponModel.valid_until > now)
            )
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    def _apply_filters(self, query, *, store_id=None, is_active=None, search=None):
        """Apply shared filter predicates to a coupon query."""
        if store_id:
            query = query.where(CouponModel.store_id == store_id)
        if is_active is not None:
            query = query.where(CouponModel.is_active == is_active)
        if search:
            search_term = f"%{search}%"
            query = query.where(CouponModel.code.ilike(search_term))
        return query

    async def list_with_filters(
        self,
        store_id: UUID | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Coupon]:
        """List coupons with multiple optional filters."""
        query = select(CouponModel)
        query = self._apply_filters(
            query, store_id=store_id, is_active=is_active, search=search
        )
        query = query.order_by(CouponModel.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_with_filters(
        self,
        store_id: UUID | None = None,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> int:
        """Count coupons matching the given filters."""
        query = select(func.count(CouponModel.id))
        query = self._apply_filters(
            query, store_id=store_id, is_active=is_active, search=search
        )
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def increment_usage(self, coupon_id: UUID) -> None:
        """Atomically increment the usage count of a coupon."""
        await self.session.execute(
            update(CouponModel)
            .where(CouponModel.id == coupon_id)
            .values(usage_count=CouponModel.usage_count + 1)
        )
        await self.session.flush()

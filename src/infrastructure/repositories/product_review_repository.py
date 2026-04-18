"""Product review repository implementation."""

from uuid import UUID, uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.product_review import ProductReview
from src.core.interfaces.repositories.product_review_repository import (
    IProductReviewRepository,
    ReviewStats,
)
from src.infrastructure.database.models.tenant.product_review import (
    ProductReviewModel,
)


class ProductReviewRepository(IProductReviewRepository):
    """SQLAlchemy-backed product review repository."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: ProductReviewModel) -> ProductReview:
        return ProductReview(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            product_id=model.product_id,
            customer_id=model.customer_id,
            reviewer_name=model.reviewer_name,
            rating=model.rating,
            title=model.title,
            body=model.body,
            is_approved=model.is_approved,
            helpful_count=model.helpful_count,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create(self, review: ProductReview) -> ProductReview:
        model = ProductReviewModel(
            id=review.id or uuid4(),
            tenant_id=review.tenant_id,
            store_id=review.store_id,
            product_id=review.product_id,
            customer_id=review.customer_id,
            reviewer_name=review.reviewer_name,
            rating=review.rating,
            title=review.title,
            body=review.body,
            is_approved=review.is_approved,
            helpful_count=review.helpful_count,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def list_for_product(
        self,
        product_id: UUID,
        approved_only: bool = True,
        skip: int = 0,
        limit: int = 50,
    ) -> list[ProductReview]:
        stmt = select(ProductReviewModel).where(
            ProductReviewModel.product_id == product_id
        )
        if approved_only:
            stmt = stmt.where(ProductReviewModel.is_approved.is_(True))
        stmt = (
            stmt.order_by(desc(ProductReviewModel.created_at)).offset(skip).limit(limit)
        )
        result = await self.session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def stats_for_product(
        self,
        product_id: UUID,
        approved_only: bool = True,
    ) -> ReviewStats:
        # Single pass: average + count
        base = select(
            func.avg(ProductReviewModel.rating).label("avg"),
            func.count(ProductReviewModel.id).label("cnt"),
        ).where(ProductReviewModel.product_id == product_id)
        if approved_only:
            base = base.where(ProductReviewModel.is_approved.is_(True))
        row = (await self.session.execute(base)).one()
        avg_value = float(row.avg) if row.avg is not None else 0.0
        count = int(row.cnt or 0)

        # Second pass: distribution by star
        dist_stmt = (
            select(
                ProductReviewModel.rating,
                func.count(ProductReviewModel.id),
            )
            .where(ProductReviewModel.product_id == product_id)
            .group_by(ProductReviewModel.rating)
        )
        if approved_only:
            dist_stmt = dist_stmt.where(ProductReviewModel.is_approved.is_(True))
        dist: dict[int, int] = dict.fromkeys(range(1, 6), 0)
        for rating, c in (await self.session.execute(dist_stmt)).all():
            if 1 <= rating <= 5:
                dist[int(rating)] = int(c)

        return ReviewStats(average=avg_value, count=count, distribution=dist)

    async def customer_has_reviewed(
        self,
        product_id: UUID,
        customer_id: UUID,
    ) -> bool:
        stmt = (
            select(ProductReviewModel.id)
            .where(ProductReviewModel.product_id == product_id)
            .where(ProductReviewModel.customer_id == customer_id)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

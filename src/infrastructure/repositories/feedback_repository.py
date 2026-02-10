"""Feedback repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.feedback import Feedback, FeedbackCategory
from src.infrastructure.database.models.public.feedback import FeedbackModel


class FeedbackRepository:
    """Feedback repository using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: FeedbackModel) -> Feedback:
        return Feedback(
            id=model.id,
            store_id=model.store_id,
            user_id=model.user_id,
            category=model.category,
            rating=model.rating,
            title=model.title,
            body=model.body,
            contact_ok=model.contact_ok,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create(self, entity: Feedback) -> Feedback:
        model = FeedbackModel(
            id=entity.id,
            store_id=entity.store_id,
            user_id=entity.user_id,
            category=entity.category,
            rating=entity.rating,
            title=entity.title,
            body=entity.body,
            contact_ok=entity.contact_ok,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def list_all(
        self,
        *,
        store_id: UUID | None = None,
        category: FeedbackCategory | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Feedback]:
        query = select(FeedbackModel)

        if store_id is not None:
            query = query.where(FeedbackModel.store_id == store_id)
        if category is not None:
            query = query.where(FeedbackModel.category == category)

        query = (
            query.order_by(FeedbackModel.created_at.desc()).offset(skip).limit(limit)
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count(
        self,
        *,
        store_id: UUID | None = None,
        category: FeedbackCategory | None = None,
    ) -> int:
        query = select(func.count(FeedbackModel.id))
        if store_id is not None:
            query = query.where(FeedbackModel.store_id == store_id)
        if category is not None:
            query = query.where(FeedbackModel.category == category)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def average_rating(self, store_id: UUID | None = None) -> float:
        query = select(func.avg(FeedbackModel.rating))
        if store_id is not None:
            query = query.where(FeedbackModel.store_id == store_id)
        result = await self.session.execute(query)
        return round(float(result.scalar() or 0), 2)

    async def category_breakdown(self, store_id: UUID | None = None) -> dict[str, int]:
        query = select(
            FeedbackModel.category,
            func.count(FeedbackModel.id),
        ).group_by(FeedbackModel.category)

        if store_id is not None:
            query = query.where(FeedbackModel.store_id == store_id)

        result = await self.session.execute(query)
        return {str(row[0]): row[1] for row in result.all()}

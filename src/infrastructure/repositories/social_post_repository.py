"""Social post repository implementation."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.social_post import SocialPost
from src.core.interfaces.repositories.social_post_repository import (
    ISocialPostRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.social_post import SocialPostModel


class SocialPostRepository(ISocialPostRepository):
    """Social post repository using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(SocialPostModel.tenant_id == tid)
        return query

    def _to_entity(self, model: SocialPostModel) -> SocialPost:
        return SocialPost(
            id=model.id,
            social_connection_id=model.social_connection_id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            platform_post_id=model.platform_post_id,
            image_url=model.image_url,
            caption=model.caption,
            likes=model.likes,
            comments=model.comments,
            posted_at=model.posted_at,
            suggested_name=model.suggested_name,
            suggested_name_ar=model.suggested_name_ar,
            suggested_price=model.suggested_price,
            imported_at=model.imported_at,
            product_id=model.product_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: SocialPost) -> SocialPostModel:
        return SocialPostModel(
            id=entity.id,
            social_connection_id=entity.social_connection_id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            platform_post_id=entity.platform_post_id,
            image_url=entity.image_url,
            caption=entity.caption,
            likes=entity.likes,
            comments=entity.comments,
            posted_at=entity.posted_at,
            suggested_name=entity.suggested_name,
            suggested_name_ar=entity.suggested_name_ar,
            suggested_price=entity.suggested_price,
            imported_at=entity.imported_at,
            product_id=entity.product_id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> SocialPost | None:
        query = select(SocialPostModel).where(SocialPostModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[SocialPost]:
        query = select(SocialPostModel).offset(skip).limit(limit)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: SocialPost) -> SocialPost:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: SocialPost) -> SocialPost:
        query = select(SocialPostModel).where(SocialPostModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            model.image_url = entity.image_url
            model.caption = entity.caption
            model.likes = entity.likes
            model.comments = entity.comments
            model.posted_at = entity.posted_at
            model.imported_at = entity.imported_at
            model.product_id = entity.product_id
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"SocialPost with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        query = select(SocialPostModel).where(SocialPostModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(SocialPostModel.id)))
        return result.scalar() or 0

    async def get_by_connection(
        self,
        connection_id: UUID,
        skip: int = 0,
        limit: int = 200,
    ) -> list[SocialPost]:
        query = (
            select(SocialPostModel)
            .where(SocialPostModel.social_connection_id == connection_id)
            .order_by(SocialPostModel.posted_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_platform_post_id(
        self,
        connection_id: UUID,
        platform_post_id: str,
    ) -> SocialPost | None:
        query = select(SocialPostModel).where(
            SocialPostModel.social_connection_id == connection_id,
            SocialPostModel.platform_post_id == platform_post_id,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_unimported(
        self,
        connection_id: UUID,
        skip: int = 0,
        limit: int = 200,
    ) -> list[SocialPost]:
        query = (
            select(SocialPostModel)
            .where(
                SocialPostModel.social_connection_id == connection_id,
                SocialPostModel.imported_at.is_(None),
            )
            .order_by(SocialPostModel.posted_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def mark_imported(
        self,
        post_id: UUID,
        product_id: UUID,
    ) -> SocialPost | None:
        await self.session.execute(
            update(SocialPostModel)
            .where(SocialPostModel.id == post_id)
            .values(
                imported_at=datetime.now(UTC),
                product_id=product_id,
            )
        )
        await self.session.flush()
        return await self.get_by_id(post_id)

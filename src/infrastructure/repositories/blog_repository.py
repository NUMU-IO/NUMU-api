"""Blog + Article repository implementations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.blog import Article, ArticleStatus, Blog
from src.core.interfaces.repositories.blog_repository import (
    IArticleRepository,
    IBlogRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.blog import ArticleModel, BlogModel


class BlogRepository(IBlogRepository):
    """Blog repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(BlogModel.tenant_id == tid)
        return query

    def _to_entity(self, model: BlogModel) -> Blog:
        return Blog(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            handle=model.handle,
            title=model.title or {},
            description=model.description or {},
            is_published=model.is_published,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Blog) -> BlogModel:
        return BlogModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            handle=entity.handle,
            title=entity.title or {},
            description=entity.description or {},
            is_published=entity.is_published,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Blog | None:
        query = select(BlogModel).where(BlogModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Blog]:
        query = select(BlogModel).order_by(BlogModel.handle).offset(skip).limit(limit)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: Blog) -> Blog:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Blog) -> Blog:
        query = select(BlogModel).where(BlogModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            # Handle is immutable in v1 (article URLs embed it) — not copied.
            model.title = entity.title or {}
            model.description = entity.description or {}
            model.is_published = entity.is_published
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Blog with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        query = select(BlogModel).where(BlogModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(BlogModel.id)))
        return result.scalar() or 0

    async def get_by_store(
        self, store_id: UUID, include_unpublished: bool = True
    ) -> list[Blog]:
        query = select(BlogModel).where(BlogModel.store_id == store_id)
        if not include_unpublished:
            query = query.where(BlogModel.is_published.is_(True))
        query = query.order_by(BlogModel.handle)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_handle(self, store_id: UUID, handle: str) -> Blog | None:
        query = select(BlogModel).where(
            BlogModel.store_id == store_id,
            BlogModel.handle == handle,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None


class ArticleRepository(IArticleRepository):
    """Article repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(ArticleModel.tenant_id == tid)
        return query

    def _to_entity(self, model: ArticleModel) -> Article:
        return Article(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            blog_id=model.blog_id,
            handle=model.handle,
            title=model.title or {},
            excerpt=model.excerpt or {},
            body=model.body or {},
            image_url=model.image_url,
            author=model.author,
            tags=list(model.tags or []),
            seo=model.seo or {},
            status=ArticleStatus(model.status),
            published_at=model.published_at,
            scheduled_at=model.scheduled_at,
            previous_handles=list(model.previous_handles or []),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Article) -> ArticleModel:
        return ArticleModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            blog_id=entity.blog_id,
            handle=entity.handle,
            title=entity.title or {},
            excerpt=entity.excerpt or {},
            body=entity.body or {},
            image_url=entity.image_url,
            author=entity.author,
            tags=list(entity.tags or []),
            seo=entity.seo or {},
            status=entity.status.value,
            published_at=entity.published_at,
            scheduled_at=entity.scheduled_at,
            previous_handles=list(entity.previous_handles or []),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    def _apply(self, model: ArticleModel, entity: Article) -> None:
        model.handle = entity.handle
        model.title = entity.title or {}
        model.excerpt = entity.excerpt or {}
        model.body = entity.body or {}
        model.image_url = entity.image_url
        model.author = entity.author
        model.tags = list(entity.tags or [])
        model.seo = entity.seo or {}
        model.status = entity.status.value
        model.published_at = entity.published_at
        model.scheduled_at = entity.scheduled_at
        model.previous_handles = list(entity.previous_handles or [])

    async def get_by_id(self, entity_id: UUID) -> Article | None:
        query = select(ArticleModel).where(ArticleModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Article]:
        query = (
            select(ArticleModel)
            .order_by(ArticleModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: Article) -> Article:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Article) -> Article:
        query = select(ArticleModel).where(ArticleModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            self._apply(model, entity)
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Article with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        query = select(ArticleModel).where(ArticleModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(ArticleModel.id)))
        return result.scalar() or 0

    async def get_by_blog(
        self,
        blog_id: UUID,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Article]:
        query = select(ArticleModel).where(ArticleModel.blog_id == blog_id)
        if status:
            query = query.where(ArticleModel.status == status)
        query = (
            query.order_by(
                nulls_last(ArticleModel.published_at.desc()),
                ArticleModel.created_at.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_handle(self, blog_id: UUID, handle: str) -> Article | None:
        query = select(ArticleModel).where(
            ArticleModel.blog_id == blog_id,
            ArticleModel.handle == handle,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def find_by_previous_handle(
        self, blog_id: UUID, handle: str
    ) -> Article | None:
        # JSONB containment: previous_handles @> '["<handle>"]'
        query = select(ArticleModel).where(
            ArticleModel.blog_id == blog_id,
            ArticleModel.previous_handles.contains([handle]),
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def list_published(
        self, blog_id: UUID, skip: int = 0, limit: int = 100
    ) -> list[Article]:
        query = (
            select(ArticleModel)
            .where(
                ArticleModel.blog_id == blog_id,
                ArticleModel.status == ArticleStatus.PUBLISHED.value,
            )
            .order_by(
                nulls_last(ArticleModel.published_at.desc()),
                ArticleModel.created_at.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_published_for_store(
        self, store_id: UUID, limit: int = 500
    ) -> list[Article]:
        query = (
            select(ArticleModel)
            .where(
                ArticleModel.store_id == store_id,
                ArticleModel.status == ArticleStatus.PUBLISHED.value,
            )
            .order_by(nulls_last(ArticleModel.published_at.desc()))
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_by_blog_for_store(self, store_id: UUID) -> dict[UUID, int]:
        query = (
            select(ArticleModel.blog_id, func.count(ArticleModel.id))
            .where(ArticleModel.store_id == store_id)
            .group_by(ArticleModel.blog_id)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return {row[0]: row[1] for row in result.all()}

    async def due_scheduled(self, now: datetime, limit: int = 200) -> list[Article]:
        # Deliberately NO tenant filter: the Celery publisher runs with no
        # tenant context and must promote every store's due articles.
        query = (
            select(ArticleModel)
            .where(
                ArticleModel.status == ArticleStatus.SCHEDULED.value,
                ArticleModel.scheduled_at.is_not(None),
                ArticleModel.scheduled_at <= now,
            )
            .order_by(ArticleModel.scheduled_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

"""Page repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.page import Page
from src.core.interfaces.repositories.page_repository import IPageRepository
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.page import PageModel


class PageRepository(IPageRepository):
    """Page repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(PageModel.tenant_id == tid)
        return query

    def _to_entity(self, model: PageModel) -> Page:
        return Page(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            handle=model.handle,
            title=model.title or {},
            body=model.body or {},
            seo=model.seo or {},
            is_published=model.is_published,
            template=model.template or "page",
            content_v3=model.content_v3 or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Page) -> PageModel:
        return PageModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            handle=entity.handle,
            title=entity.title or {},
            body=entity.body or {},
            seo=entity.seo or {},
            is_published=entity.is_published,
            template=entity.template or "page",
            content_v3=entity.content_v3 or {},
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Page | None:
        query = select(PageModel).where(PageModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Page]:
        query = select(PageModel).order_by(PageModel.handle).offset(skip).limit(limit)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: Page) -> Page:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Page) -> Page:
        query = select(PageModel).where(PageModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            model.handle = entity.handle
            model.title = entity.title or {}
            model.body = entity.body or {}
            model.seo = entity.seo or {}
            model.is_published = entity.is_published
            model.template = entity.template or "page"
            model.content_v3 = entity.content_v3 or {}
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Page with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        query = select(PageModel).where(PageModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(PageModel.id)))
        return result.scalar() or 0

    async def get_by_store(
        self, store_id: UUID, include_unpublished: bool = True
    ) -> list[Page]:
        query = select(PageModel).where(PageModel.store_id == store_id)
        if not include_unpublished:
            query = query.where(PageModel.is_published.is_(True))
        query = query.order_by(PageModel.handle)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_handle(self, store_id: UUID, handle: str) -> Page | None:
        query = select(PageModel).where(
            PageModel.store_id == store_id,
            PageModel.handle == handle,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

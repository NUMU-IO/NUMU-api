"""Category repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.category import Category
from src.core.interfaces.repositories.category_repository import ICategoryRepository
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.category import CategoryModel
from src.infrastructure.database.models.tenant.product import ProductModel


class CategoryRepository(ICategoryRepository):
    """Category repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(CategoryModel.tenant_id == tid)
        return query

    def _to_entity(self, model: CategoryModel) -> Category:
        """Convert database model to domain entity."""
        return Category(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            name=model.name,
            slug=model.slug,
            previous_slugs=list(model.previous_slugs or []),
            description=model.description,
            image_url=model.image_url,
            parent_id=model.parent_id,
            position=model.position,
            is_active=model.is_active,
            seo_title=model.seo_title,
            seo_description=model.seo_description,
            social_image_url=model.social_image_url,
            template_suffix=model.template_suffix,
            metadata=model.extra_data or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Category) -> CategoryModel:
        """Convert domain entity to database model."""
        return CategoryModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            name=entity.name,
            slug=entity.slug,
            previous_slugs=list(entity.previous_slugs or []),
            description=entity.description,
            image_url=entity.image_url,
            parent_id=entity.parent_id,
            position=entity.position,
            is_active=entity.is_active,
            seo_title=entity.seo_title,
            seo_description=entity.seo_description,
            social_image_url=entity.social_image_url,
            template_suffix=entity.template_suffix,
            extra_data=entity.metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Category | None:
        query = select(CategoryModel).where(CategoryModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Category]:
        query = (
            select(CategoryModel)
            .order_by(CategoryModel.position, CategoryModel.name)
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: Category) -> Category:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Category) -> Category:
        query = select(CategoryModel).where(CategoryModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            model.name = entity.name
            model.slug = entity.slug
            model.previous_slugs = list(entity.previous_slugs or [])
            model.description = entity.description
            model.image_url = entity.image_url
            model.parent_id = entity.parent_id
            model.position = entity.position
            model.is_active = entity.is_active
            model.seo_title = entity.seo_title
            model.seo_description = entity.seo_description
            model.social_image_url = entity.social_image_url
            model.template_suffix = entity.template_suffix
            model.extra_data = entity.metadata
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Category with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        query = select(CategoryModel).where(CategoryModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(CategoryModel.id)))
        return result.scalar() or 0

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        include_inactive: bool = False,
    ) -> list[Category]:
        query = select(CategoryModel).where(CategoryModel.store_id == store_id)
        if not include_inactive:
            query = query.where(CategoryModel.is_active.is_(True))
        query = (
            query.order_by(CategoryModel.position, CategoryModel.name)
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_slug(self, store_id: UUID, slug: str) -> Category | None:
        query = select(CategoryModel).where(
            CategoryModel.store_id == store_id,
            CategoryModel.slug == slug,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def find_by_previous_slug(self, store_id: UUID, slug: str) -> Category | None:
        """Get the category that USED to live at ``slug`` (rename history).

        Called only after ``get_by_slug`` misses, so the storefront can 301 an
        old collection URL to the current one instead of 404ing it.
        """
        # JSONB containment: previous_slugs @> '["<slug>"]'
        query = select(CategoryModel).where(
            CategoryModel.store_id == store_id,
            CategoryModel.previous_slugs.contains([slug]),
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def get_children(self, parent_id: UUID) -> list[Category]:
        query = (
            select(CategoryModel)
            .where(CategoryModel.parent_id == parent_id)
            .order_by(CategoryModel.position, CategoryModel.name)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_root_categories(self, store_id: UUID) -> list[Category]:
        query = (
            select(CategoryModel)
            .where(
                CategoryModel.store_id == store_id,
                CategoryModel.parent_id.is_(None),
            )
            .order_by(CategoryModel.position, CategoryModel.name)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_by_store(self, store_id: UUID) -> int:
        query = select(func.count(CategoryModel.id)).where(
            CategoryModel.store_id == store_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

    async def get_product_counts(self, store_id: UUID) -> dict[UUID, int]:
        """Get product count per category for a store."""
        query = (
            select(
                ProductModel.category_id,
                func.count(ProductModel.id).label("cnt"),
            )
            .where(
                ProductModel.store_id == store_id,
                ProductModel.category_id.isnot(None),
            )
            .group_by(ProductModel.category_id)
        )
        result = await self.session.execute(query)
        return {row[0]: row[1] for row in result.all()}

"""Store repository implementation."""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.store import Store, StoreStatus
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.value_objects.money import Currency
from src.infrastructure.database.models import StoreModel


class StoreRepository(IStoreRepository):
    """Store repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: StoreModel) -> Store:
        """Convert database model to domain entity."""
        return Store(
            id=model.id,
            name=model.name,
            slug=model.slug,
            owner_id=model.owner_id,
            description=model.description,
            logo_url=model.logo_url,
            banner_url=model.banner_url,
            status=model.status,
            default_currency=model.default_currency,
            contact_email=model.contact_email,
            contact_phone=model.contact_phone,
            address=model.address,
            social_links=model.social_links,
            settings=model.settings,
            tenant_id=model.tenant_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Store) -> StoreModel:
        """Convert domain entity to database model."""
        return StoreModel(
            id=entity.id,
            name=entity.name,
            slug=entity.slug,
            owner_id=entity.owner_id,
            description=entity.description,
            logo_url=entity.logo_url,
            banner_url=entity.banner_url,
            status=entity.status,
            default_currency=entity.default_currency,
            contact_email=entity.contact_email,
            contact_phone=entity.contact_phone,
            address=entity.address,
            social_links=entity.social_links,
            settings=entity.settings,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Store | None:
        """Get store by ID."""
        result = await self.session.execute(
            select(StoreModel).where(StoreModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Store]:
        """Get all stores with pagination."""
        result = await self.session.execute(
            select(StoreModel).offset(skip).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: Store) -> Store:
        """Create a new store."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Store) -> Store:
        """Update an existing store."""
        result = await self.session.execute(
            select(StoreModel).where(StoreModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.name = entity.name
            model.slug = entity.slug
            model.description = entity.description
            model.logo_url = entity.logo_url
            model.banner_url = entity.banner_url
            model.status = entity.status
            model.default_currency = entity.default_currency
            model.contact_email = entity.contact_email
            model.contact_phone = entity.contact_phone
            model.address = entity.address
            model.social_links = entity.social_links
            model.settings = entity.settings
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Store with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a store by ID."""
        result = await self.session.execute(
            select(StoreModel).where(StoreModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of stores."""
        result = await self.session.execute(
            select(func.count(StoreModel.id))
        )
        return result.scalar() or 0

    async def get_by_slug(self, slug: str) -> Store | None:
        """Get store by slug."""
        result = await self.session.execute(
            select(StoreModel).where(StoreModel.slug == slug)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def slug_exists(self, slug: str) -> bool:
        """Check if slug already exists."""
        result = await self.session.execute(
            select(StoreModel.id).where(StoreModel.slug == slug)
        )
        return result.scalar_one_or_none() is not None

    async def get_by_owner(
        self,
        owner_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Store]:
        """Get all stores owned by a user."""
        result = await self.session.execute(
            select(StoreModel)
            .where(StoreModel.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def search(
        self,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Store]:
        """Search stores by name."""
        search_term = f"%{query}%"
        result = await self.session.execute(
            select(StoreModel)
            .where(
                or_(
                    StoreModel.name.ilike(search_term),
                    StoreModel.description.ilike(search_term),
                )
            )
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

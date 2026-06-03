"""Menu repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.menu import Menu
from src.core.interfaces.repositories.menu_repository import IMenuRepository
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.menu import MenuModel


class MenuRepository(IMenuRepository):
    """Menu repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(MenuModel.tenant_id == tid)
        return query

    def _to_entity(self, model: MenuModel) -> Menu:
        return Menu(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            handle=model.handle,
            title=model.title or {},
            items=model.items or [],
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Menu) -> MenuModel:
        return MenuModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            handle=entity.handle,
            title=entity.title or {},
            items=entity.items or [],
            is_active=entity.is_active,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Menu | None:
        query = select(MenuModel).where(MenuModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Menu]:
        query = select(MenuModel).order_by(MenuModel.handle).offset(skip).limit(limit)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: Menu) -> Menu:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Menu) -> Menu:
        query = select(MenuModel).where(MenuModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            model.handle = entity.handle
            model.title = entity.title or {}
            model.items = entity.items or []
            model.is_active = entity.is_active
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Menu with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        query = select(MenuModel).where(MenuModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(MenuModel.id)))
        return result.scalar() or 0

    async def get_by_store(
        self, store_id: UUID, include_inactive: bool = False
    ) -> list[Menu]:
        query = select(MenuModel).where(MenuModel.store_id == store_id)
        if not include_inactive:
            query = query.where(MenuModel.is_active.is_(True))
        query = query.order_by(MenuModel.handle)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_handle(self, store_id: UUID, handle: str) -> Menu | None:
        query = select(MenuModel).where(
            MenuModel.store_id == store_id,
            MenuModel.handle == handle,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

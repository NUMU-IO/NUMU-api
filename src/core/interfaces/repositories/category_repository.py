"""Category repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.category import Category
from src.core.interfaces.repositories.base import BaseRepository


class ICategoryRepository(BaseRepository[Category]):
    """Category repository interface."""

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        include_inactive: bool = False,
    ) -> list[Category]:
        """Get all categories for a store."""
        ...

    @abstractmethod
    async def get_by_slug(self, store_id: UUID, slug: str) -> Category | None:
        """Get category by slug within a store."""
        ...

    @abstractmethod
    async def get_children(self, parent_id: UUID) -> list[Category]:
        """Get child categories of a parent."""
        ...

    @abstractmethod
    async def get_root_categories(self, store_id: UUID) -> list[Category]:
        """Get root categories (no parent) for a store."""
        ...

    @abstractmethod
    async def count_by_store(self, store_id: UUID) -> int:
        """Get total count of categories for a store."""
        ...

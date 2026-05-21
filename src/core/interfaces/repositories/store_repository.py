"""Store repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.store import Store
from src.core.interfaces.repositories.base import BaseRepository


class IStoreRepository(BaseRepository[Store]):
    """Store repository interface."""

    @abstractmethod
    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        is_active: bool | None = None,
    ) -> list[Store]:
        """Get all stores with optional filtering."""
        ...

    @abstractmethod
    async def count(self, is_active: bool | None = None) -> int:
        """Get total count of stores, optionally filtered."""
        ...

    @abstractmethod
    async def get_by_slug(self, slug: str) -> Store | None:
        """Get store by slug."""
        ...

    @abstractmethod
    async def slug_exists(self, slug: str) -> bool:
        """Check if slug already exists."""
        ...

    @abstractmethod
    async def get_by_owner(
        self,
        owner_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Store]:
        """Get all stores owned by a user."""
        ...

    @abstractmethod
    async def get_accessible_by_user(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Store]:
        """Get stores the user can access — owned OR active tenant member."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Store]:
        """Search stores by name."""
        ...

"""Menu repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.menu import Menu
from src.core.interfaces.repositories.base import BaseRepository


class IMenuRepository(BaseRepository[Menu]):
    """Store navigation menu repository interface."""

    @abstractmethod
    async def get_by_store(
        self, store_id: UUID, include_inactive: bool = False
    ) -> list[Menu]:
        """Get all menus for a store, ordered by handle."""
        ...

    @abstractmethod
    async def get_by_handle(self, store_id: UUID, handle: str) -> Menu | None:
        """Get a single menu by its handle within a store."""
        ...

"""Page repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.page import Page
from src.core.interfaces.repositories.base import BaseRepository


class IPageRepository(BaseRepository[Page]):
    """Merchant content page repository interface."""

    @abstractmethod
    async def get_by_store(
        self, store_id: UUID, include_unpublished: bool = True
    ) -> list[Page]:
        """Get all pages for a store, ordered by handle."""
        ...

    @abstractmethod
    async def get_by_handle(self, store_id: UUID, handle: str) -> Page | None:
        """Get a single page by its handle within a store."""
        ...

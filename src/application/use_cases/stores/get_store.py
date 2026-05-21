"""Get store use case."""

from uuid import UUID

from src.application.dto.store import StoreDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.store_repository import IStoreRepository


class GetStoreUseCase:
    """Use case for getting a store."""

    def __init__(self, store_repository: IStoreRepository) -> None:
        self.store_repository = store_repository

    async def execute(self, store_id: UUID) -> StoreDTO:
        """Get a store by ID."""
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        return StoreDTO.from_entity(store)

    async def by_slug(self, slug: str) -> StoreDTO:
        """Get a store by slug."""
        store = await self.store_repository.get_by_slug(slug)
        if not store:
            raise EntityNotFoundError("Store", slug, identifier_name="slug")
        return StoreDTO.from_entity(store)

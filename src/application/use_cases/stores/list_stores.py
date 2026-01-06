"""List stores use case."""

from uuid import UUID

from src.application.dto.base import PaginatedDTO
from src.application.dto.store import StoreDTO
from src.core.interfaces.repositories.store_repository import IStoreRepository


class ListStoresUseCase:
    """Use case for listing stores."""

    def __init__(self, store_repository: IStoreRepository) -> None:
        self.store_repository = store_repository

    async def execute(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedDTO:
        """List all stores with pagination."""
        skip = (page - 1) * page_size
        stores = await self.store_repository.get_all(skip=skip, limit=page_size)
        total = await self.store_repository.count()

        return PaginatedDTO.create(
            items=[StoreDTO.from_entity(store) for store in stores],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def by_owner(
        self,
        owner_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedDTO:
        """List stores by owner with pagination."""
        skip = (page - 1) * page_size
        stores = await self.store_repository.get_by_owner(
            owner_id=owner_id,
            skip=skip,
            limit=page_size,
        )
        # Note: We'd need a count_by_owner method for accurate total
        total = len(stores)

        return PaginatedDTO.create(
            items=[StoreDTO.from_entity(store) for store in stores],
            total=total,
            page=page,
            page_size=page_size,
        )

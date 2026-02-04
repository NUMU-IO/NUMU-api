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
        skip: int = 0,
        limit: int = 20,
        is_active: bool | None = None,
    ) -> PaginatedDTO:
        """List all stores with pagination and filtering.

        Args:
            skip: Number of records to skip (for pagination)
            limit: Maximum number of records to return
            is_active: Optional filter for active/inactive stores

        Returns:
            PaginatedDTO containing store data and pagination metadata
        """
        # Apply filters through repository
        stores = await self.store_repository.get_all(
            skip=skip,
            limit=limit,
            is_active=is_active,
        )

        # Get total count (potentially filtered)
        total = await self.store_repository.count(is_active=is_active)

        # Calculate page number from skip/limit for response metadata
        page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedDTO.create(
            items=[StoreDTO.from_entity(store) for store in stores],
            total=total,
            page=page,
            page_size=limit,
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

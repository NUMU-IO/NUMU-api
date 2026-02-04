"""List products use case."""

from uuid import UUID

from src.application.dto.base import PaginatedDTO
from src.application.dto.product import ProductDTO
from src.core.interfaces.repositories.product_repository import IProductRepository


class ListProductsUseCase:
    """Use case for listing products."""

    def __init__(self, product_repository: IProductRepository) -> None:
        self.product_repository = product_repository

    async def execute(
        self,
        store_id: UUID | None = None,
        category_id: UUID | None = None,
        skip: int = 0,
        limit: int = 20,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> PaginatedDTO:
        """List products with filtering and pagination.

        Args:
            store_id: Optional filter by store
            category_id: Optional filter by category
            skip: Number of records to skip (for pagination)
            limit: Maximum number of records to return
            is_active: Optional filter for active/inactive products
            search: Optional search query for product name/description

        Returns:
            PaginatedDTO containing product data and pagination metadata
        """
        # Build filters for repository query
        products = await self.product_repository.list_with_filters(
            store_id=store_id,
            category_id=category_id,
            skip=skip,
            limit=limit,
            is_active=is_active,
            search=search,
        )

        # Get total count with same filters
        total = await self.product_repository.count_with_filters(
            store_id=store_id,
            category_id=category_id,
            is_active=is_active,
            search=search,
        )

        # Calculate page number from skip/limit for response metadata
        page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedDTO.create(
            items=[ProductDTO.from_entity(product) for product in products],
            total=total,
            page=page,
            page_size=limit,
        )

    async def search(
        self,
        store_id: UUID,
        query: str,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedDTO:
        """Search products in a store."""
        skip = (page - 1) * page_size
        products = await self.product_repository.search(
            store_id=store_id,
            query=query,
            skip=skip,
            limit=page_size,
        )

        return PaginatedDTO.create(
            items=[ProductDTO.from_entity(product) for product in products],
            total=len(products),  # Would need a search count method for accuracy
            page=page,
            page_size=page_size,
        )

    async def by_category(
        self,
        category_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedDTO:
        """List products by category."""
        skip = (page - 1) * page_size
        products = await self.product_repository.get_by_category(
            category_id=category_id,
            skip=skip,
            limit=page_size,
        )

        return PaginatedDTO.create(
            items=[ProductDTO.from_entity(product) for product in products],
            total=len(products),
            page=page,
            page_size=page_size,
        )

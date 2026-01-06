"""List products use case."""

from uuid import UUID

from src.application.dto.base import PaginatedDTO
from src.application.dto.product import ProductDTO
from src.core.entities.product import ProductStatus
from src.core.interfaces.repositories.product_repository import IProductRepository


class ListProductsUseCase:
    """Use case for listing products."""

    def __init__(self, product_repository: IProductRepository) -> None:
        self.product_repository = product_repository

    async def execute(
        self,
        store_id: UUID,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> PaginatedDTO:
        """List products for a store with pagination."""
        skip = (page - 1) * page_size
        
        # Parse status if provided
        product_status = None
        if status:
            try:
                product_status = ProductStatus(status)
            except ValueError:
                pass

        products = await self.product_repository.get_by_store(
            store_id=store_id,
            skip=skip,
            limit=page_size,
            status=product_status,
        )
        total = await self.product_repository.count_by_store(store_id)

        return PaginatedDTO.create(
            items=[ProductDTO.from_entity(product) for product in products],
            total=total,
            page=page,
            page_size=page_size,
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

"""Get product use case."""

from uuid import UUID

from src.application.dto.product import ProductDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.product_repository import IProductRepository


class GetProductUseCase:
    """Use case for getting a product."""

    def __init__(self, product_repository: IProductRepository) -> None:
        self.product_repository = product_repository

    async def execute(self, product_id: UUID) -> ProductDTO:
        """Get a product by ID."""
        product = await self.product_repository.get_by_id(product_id)
        if not product:
            raise EntityNotFoundError("Product", str(product_id))
        return ProductDTO.from_entity(product)

    async def by_slug(self, store_id: UUID, slug: str) -> ProductDTO:
        """Get a product by slug within a store."""
        product = await self.product_repository.get_by_slug(store_id, slug)
        if not product:
            raise EntityNotFoundError("Product", slug)
        return ProductDTO.from_entity(product)

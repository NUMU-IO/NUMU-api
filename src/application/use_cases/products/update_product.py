"""Update product use case."""

from uuid import UUID

from src.application.dto.product import ProductDTO, UpdateProductDTO
from src.core.entities.product import ProductStatus
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.value_objects.money import Money


class UpdateProductUseCase:
    """Use case for updating a product."""

    def __init__(
        self,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.product_repository = product_repository
        self.store_repository = store_repository

    async def execute(
        self,
        product_id: UUID,
        dto: UpdateProductDTO,
        user_id: UUID,
    ) -> ProductDTO:
        """Update a product."""
        # Get product
        product = await self.product_repository.get_by_id(product_id)
        if not product:
            raise EntityNotFoundError("Product", str(product_id))

        # Get store and verify ownership
        store = await self.store_repository.get_by_id(product.store_id)
        if not store or store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to update this product")

        # Update fields
        if dto.name is not None:
            product.name = dto.name
        if dto.slug is not None:
            product.slug = dto.slug
        if dto.sku is not None:
            product.sku = dto.sku
        if dto.description is not None:
            product.description = dto.description
        if dto.short_description is not None:
            product.short_description = dto.short_description
        if dto.price is not None:
            product.price = Money(amount=dto.price, currency=product.price.currency)
        if dto.compare_at_price is not None:
            product.compare_at_price = Money(amount=dto.compare_at_price, currency=product.price.currency)
        if dto.cost_price is not None:
            product.cost_price = Money(amount=dto.cost_price, currency=product.price.currency)
        if dto.quantity is not None:
            product.quantity = dto.quantity
        if dto.low_stock_threshold is not None:
            product.low_stock_threshold = dto.low_stock_threshold
        if dto.images is not None:
            product.images = dto.images
        if dto.category_id is not None:
            product.category_id = dto.category_id
        if dto.tags is not None:
            product.tags = dto.tags
        if dto.attributes is not None:
            product.attributes = dto.attributes
        if dto.status is not None:
            try:
                product.status = ProductStatus(dto.status)
            except ValueError:
                pass

        # Save product
        updated_product = await self.product_repository.update(product)

        return ProductDTO.from_entity(updated_product)

"""Create product use case."""

import uuid
from uuid import UUID

from slugify import slugify

from src.application.dto.product import CreateProductDTO, ProductDTO
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.value_objects.money import Currency, Money


class CreateProductUseCase:
    """Use case for creating a new product."""

    def __init__(
        self,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.product_repository = product_repository
        self.store_repository = store_repository

    async def execute(
        self,
        dto: CreateProductDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> ProductDTO:
        """Create a new product."""
        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to add products to this store")

        # Generate slug if not provided
        slug = dto.slug or slugify(dto.name)

        # Check if slug already exists in store
        existing = await self.product_repository.get_by_slug(store_id, slug)
        if existing:
            slug = f"{slug}-{str(uuid.uuid4())[:8]}"

        # Parse currency and create Money
        try:
            currency = Currency(dto.price_currency)
        except ValueError:
            currency = Currency.USD

        price = Money(amount=dto.price, currency=currency)
        compare_at_price = Money(amount=dto.compare_at_price, currency=currency) if dto.compare_at_price else None
        cost_price = Money(amount=dto.cost_price, currency=currency) if dto.cost_price else None

        # Parse product type
        try:
            product_type = ProductType(dto.product_type)
        except ValueError:
            product_type = ProductType.PHYSICAL

        # Create product entity
        product = Product(
            store_id=store_id,
            name=dto.name,
            slug=slug,
            sku=dto.sku,
            description=dto.description,
            short_description=dto.short_description,
            product_type=product_type,
            status=ProductStatus.DRAFT,
            price=price,
            compare_at_price=compare_at_price,
            cost_price=cost_price,
            quantity=dto.quantity,
            low_stock_threshold=dto.low_stock_threshold,
            images=dto.images,
            category_id=dto.category_id,
            tags=dto.tags,
            attributes=dto.attributes,
        )

        # Save product
        created_product = await self.product_repository.create(product)

        return ProductDTO.from_entity(created_product)

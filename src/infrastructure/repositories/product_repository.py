"""Product repository implementation."""

from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.product import Product, ProductStatus
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.value_objects.money import Currency, Money
from src.infrastructure.database.models import ProductModel


class ProductRepository(IProductRepository):
    """Product repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: ProductModel) -> Product:
        """Convert database model to domain entity."""
        currency = Currency(model.price_currency) if model.price_currency else Currency.USD
        return Product(
            id=model.id,
            store_id=model.store_id,
            name=model.name,
            slug=model.slug,
            sku=model.sku,
            description=model.description,
            short_description=model.short_description,
            product_type=model.product_type,
            status=model.status,
            price=Money.from_cents(model.price_amount, currency),
            compare_at_price=Money.from_cents(model.compare_at_price, currency) if model.compare_at_price else None,
            cost_price=Money.from_cents(model.cost_price, currency) if model.cost_price else None,
            quantity=model.quantity,
            low_stock_threshold=model.low_stock_threshold,
            weight=model.weight,
            dimensions=model.dimensions,
            images=model.images or [],
            category_id=model.category_id,
            tags=model.tags or [],
            attributes=model.attributes,
            metadata=model.extra_data or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Product) -> ProductModel:
        """Convert domain entity to database model."""
        return ProductModel(
            id=entity.id,
            store_id=entity.store_id,
            name=entity.name,
            slug=entity.slug,
            sku=entity.sku,
            description=entity.description,
            short_description=entity.short_description,
            product_type=entity.product_type,
            status=entity.status,
            price_amount=entity.price.cents,
            price_currency=entity.price.currency.value,
            compare_at_price=entity.compare_at_price.cents if entity.compare_at_price else None,
            cost_price=entity.cost_price.cents if entity.cost_price else None,
            quantity=entity.quantity,
            low_stock_threshold=entity.low_stock_threshold,
            weight=entity.weight,
            dimensions=entity.dimensions,
            images=entity.images,
            category_id=entity.category_id,
            tags=entity.tags,
            attributes=entity.attributes,
            extra_data=entity.metadata,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Product | None:
        """Get product by ID."""
        result = await self.session.execute(
            select(ProductModel).where(ProductModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Product]:
        """Get all products with pagination."""
        result = await self.session.execute(
            select(ProductModel).offset(skip).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: Product) -> Product:
        """Create a new product."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Product) -> Product:
        """Update an existing product."""
        result = await self.session.execute(
            select(ProductModel).where(ProductModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.name = entity.name
            model.slug = entity.slug
            model.sku = entity.sku
            model.description = entity.description
            model.short_description = entity.short_description
            model.product_type = entity.product_type
            model.status = entity.status
            model.price_amount = entity.price.cents
            model.price_currency = entity.price.currency.value
            model.compare_at_price = entity.compare_at_price.cents if entity.compare_at_price else None
            model.cost_price = entity.cost_price.cents if entity.cost_price else None
            model.quantity = entity.quantity
            model.low_stock_threshold = entity.low_stock_threshold
            model.weight = entity.weight
            model.dimensions = entity.dimensions
            model.images = entity.images
            model.category_id = entity.category_id
            model.tags = entity.tags
            model.attributes = entity.attributes
            model.extra_data = entity.metadata
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Product with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a product by ID."""
        result = await self.session.execute(
            select(ProductModel).where(ProductModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of products."""
        result = await self.session.execute(
            select(func.count(ProductModel.id))
        )
        return result.scalar() or 0

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: ProductStatus | None = None,
    ) -> list[Product]:
        """Get all products for a store."""
        query = select(ProductModel).where(ProductModel.store_id == store_id)
        if status:
            query = query.where(ProductModel.status == status)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_by_slug(self, store_id: UUID, slug: str) -> Product | None:
        """Get product by slug within a store."""
        result = await self.session.execute(
            select(ProductModel).where(
                ProductModel.store_id == store_id,
                ProductModel.slug == slug,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_sku(self, store_id: UUID, sku: str) -> Product | None:
        """Get product by SKU within a store."""
        result = await self.session.execute(
            select(ProductModel).where(
                ProductModel.store_id == store_id,
                ProductModel.sku == sku,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_category(
        self,
        category_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Product]:
        """Get all products in a category."""
        result = await self.session.execute(
            select(ProductModel)
            .where(ProductModel.category_id == category_id)
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def search(
        self,
        store_id: UUID,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Product]:
        """Search products by name or description."""
        search_term = f"%{query}%"
        result = await self.session.execute(
            select(ProductModel)
            .where(
                ProductModel.store_id == store_id,
                or_(
                    ProductModel.name.ilike(search_term),
                    ProductModel.description.ilike(search_term),
                ),
            )
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_low_stock(
        self,
        store_id: UUID,
        threshold: int | None = None,
        limit: int = 100,
    ) -> list[Product]:
        """Get products with low stock."""
        result = await self.session.execute(
            select(ProductModel).where(
                ProductModel.store_id == store_id,
                ProductModel.quantity <= (threshold or ProductModel.low_stock_threshold),
                ProductModel.quantity > 0,
            ).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_out_of_stock(
        self,
        store_id: UUID,
        limit: int = 100,
    ) -> list[Product]:
        """Get products that are out of stock."""
        result = await self.session.execute(
            select(ProductModel).where(
                ProductModel.store_id == store_id,
                ProductModel.quantity == 0,
            ).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_by_store(self, store_id: UUID) -> int:
        """Get total count of products for a store."""
        result = await self.session.execute(
            select(func.count(ProductModel.id)).where(ProductModel.store_id == store_id)
        )
        return result.scalar() or 0

    async def bulk_update_quantity(
        self,
        updates: list[tuple[UUID, int]],
    ) -> None:
        """Bulk update product quantities."""
        for product_id, delta in updates:
            await self.session.execute(
                update(ProductModel)
                .where(ProductModel.id == product_id)
                .values(quantity=ProductModel.quantity + delta)
            )
        await self.session.flush()

    def _apply_product_filters(self, query, *, store_id=None, category_id=None,
                               is_active=None, search=None, sku=None,
                               price_min=None, price_max=None):
        """Apply shared filter predicates to a product query."""
        if store_id:
            query = query.where(ProductModel.store_id == store_id)
        if category_id:
            query = query.where(ProductModel.category_id == category_id)
        if is_active is not None:
            target_status = ProductStatus.ACTIVE if is_active else ProductStatus.DRAFT
            query = query.where(ProductModel.status == target_status)
        if sku:
            query = query.where(ProductModel.sku.ilike(f"%{sku}%"))
        if price_min is not None:
            query = query.where(ProductModel.price_amount >= price_min)
        if price_max is not None:
            query = query.where(ProductModel.price_amount <= price_max)
        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ProductModel.name.ilike(search_term),
                    ProductModel.description.ilike(search_term),
                    ProductModel.sku.ilike(search_term),
                )
            )
        return query

    @staticmethod
    def _apply_sort(query, sort_by: str | None, sort_order: str = "asc"):
        """Apply sorting to a product query."""
        sort_columns = {
            "name": ProductModel.name,
            "price": ProductModel.price_amount,
            "created_at": ProductModel.created_at,
            "updated_at": ProductModel.updated_at,
            "quantity": ProductModel.quantity,
        }
        column = sort_columns.get(sort_by, ProductModel.created_at)
        if sort_order == "desc":
            column = column.desc()
        else:
            column = column.asc()
        return query.order_by(column)

    async def list_with_filters(
        self,
        store_id: UUID | None = None,
        category_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
        is_active: bool | None = None,
        search: str | None = None,
        sku: str | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
    ) -> list[Product]:
        """List products with multiple optional filters, price range, and sorting."""
        query = select(ProductModel)
        query = self._apply_product_filters(
            query, store_id=store_id, category_id=category_id,
            is_active=is_active, search=search, sku=sku,
            price_min=price_min, price_max=price_max,
        )
        query = self._apply_sort(query, sort_by, sort_order)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_with_filters(
        self,
        store_id: UUID | None = None,
        category_id: UUID | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        sku: str | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
    ) -> int:
        """Count products matching the given filters."""
        query = select(func.count(ProductModel.id))
        query = self._apply_product_filters(
            query, store_id=store_id, category_id=category_id,
            is_active=is_active, search=search, sku=sku,
            price_min=price_min, price_max=price_max,
        )
        result = await self.session.execute(query)
        return result.scalar() or 0

"""Product repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.product import Product, ProductStatus
from src.core.interfaces.repositories.base import BaseRepository


class IProductRepository(BaseRepository[Product]):
    """Product repository interface."""

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: ProductStatus | None = None,
    ) -> list[Product]:
        """Get all products for a store."""
        ...

    @abstractmethod
    async def get_by_slug(self, store_id: UUID, slug: str) -> Product | None:
        """Get product by slug within a store."""
        ...

    @abstractmethod
    async def get_by_sku(self, store_id: UUID, sku: str) -> Product | None:
        """Get product by SKU within a store."""
        ...

    @abstractmethod
    async def get_by_category(
        self,
        category_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Product]:
        """Get all products in a category."""
        ...

    @abstractmethod
    async def search(
        self,
        store_id: UUID,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Product]:
        """Search products by name or description."""
        ...

    @abstractmethod
    async def get_low_stock(
        self,
        store_id: UUID,
        threshold: int | None = None,
    ) -> list[Product]:
        """Get products with low stock."""
        ...

    @abstractmethod
    async def count_by_store(self, store_id: UUID) -> int:
        """Get total count of products for a store."""
        ...

    @abstractmethod
    async def bulk_update_quantity(
        self,
        updates: list[tuple[UUID, int]],
    ) -> None:
        """Bulk update product quantities. Each tuple is (product_id, quantity_delta)."""
        ...

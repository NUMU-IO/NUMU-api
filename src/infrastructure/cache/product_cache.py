"""Product-specific caching layer for 3G optimization.

This module provides caching for product listings and category trees
with automatic cache invalidation on updates.

Cache Key Design:
- Products: numu:v1:products:store:{store_id}:cat:{cat_id}:f:{filter_hash}:p:{page}:l:{limit}
- Product detail: numu:v1:products:store:{store_id}:detail:{product_id}
- Categories: numu:v1:categories:store:{store_id}:tree

TTL Strategy:
- Product listings: 5 minutes (frequently updated)
- Product details: 30 minutes (less frequently accessed)
- Category trees: 1 hour (rarely changes)

Invalidation:
- On product update: invalidate product detail + all listing caches for store/category
- On category update: invalidate category tree cache
"""

import hashlib
import json
from typing import Any
from uuid import UUID

from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.observability.prometheus_metrics import (
    record_cache_hit,
    record_cache_invalidate,
    record_cache_miss,
)


class ProductCacheService:
    """Caching service for product-related data.

    Provides high-level caching methods with automatic key generation
    and cache invalidation on updates.
    """

    # Cache TTLs in seconds
    TTL_PRODUCT_LIST = 300  # 5 minutes
    TTL_PRODUCT_DETAIL = 1800  # 30 minutes
    TTL_CATEGORY_TREE = 3600  # 1 hour

    # Cache key prefix
    PREFIX = "numu:v1"

    def __init__(self, cache_service: RedisCacheService):
        """Initialize with base cache service.

        Args:
            cache_service: RedisCacheService instance for Redis operations
        """
        self.cache = cache_service

    # =========================================================================
    # Product List Caching
    # =========================================================================

    async def get_products(
        self,
        store_id: UUID,
        category_id: UUID | None,
        page: int,
        limit: int,
        filters: dict | None = None,
    ) -> dict | None:
        """Get cached product list.

        Args:
            store_id: Store identifier
            category_id: Optional category filter
            page: Page number
            limit: Items per page
            filters: Additional filter parameters

        Returns:
            Cached product list data or None if not cached
        """
        key = self._products_key(store_id, category_id, page, limit, filters)
        value = await self.cache.get(key)
        if value is None:
            record_cache_miss("product")
        else:
            record_cache_hit("product")
        return value

    async def set_products(
        self,
        store_id: UUID,
        category_id: UUID | None,
        page: int,
        limit: int,
        data: dict,
        filters: dict | None = None,
    ) -> None:
        """Cache product list.

        Args:
            store_id: Store identifier
            category_id: Optional category filter
            page: Page number
            limit: Items per page
            data: Product list data to cache
            filters: Additional filter parameters
        """
        key = self._products_key(store_id, category_id, page, limit, filters)
        await self.cache.set(key, data, expire=self.TTL_PRODUCT_LIST)

    # =========================================================================
    # Product Detail Caching
    # =========================================================================

    async def get_product(self, store_id: UUID, product_id: UUID) -> dict | None:
        """Get cached product detail.

        Args:
            store_id: Store identifier
            product_id: Product identifier

        Returns:
            Cached product data or None if not cached
        """
        key = self._product_detail_key(store_id, product_id)
        value = await self.cache.get(key)
        if value is None:
            record_cache_miss("product")
        else:
            record_cache_hit("product")
        return value

    async def set_product(self, store_id: UUID, product_id: UUID, data: dict) -> None:
        """Cache product detail.

        Args:
            store_id: Store identifier
            product_id: Product identifier
            data: Product data to cache
        """
        key = self._product_detail_key(store_id, product_id)
        await self.cache.set(key, data, expire=self.TTL_PRODUCT_DETAIL)

    # =========================================================================
    # Category Tree Caching
    # =========================================================================

    async def get_category_tree(self, store_id: UUID) -> list | None:
        """Get cached category tree.

        Args:
            store_id: Store identifier

        Returns:
            Cached category tree or None if not cached
        """
        key = self._category_tree_key(store_id)
        value = await self.cache.get(key)
        if value is None:
            record_cache_miss("product")
        else:
            record_cache_hit("product")
        return value

    async def set_category_tree(self, store_id: UUID, tree: list) -> None:
        """Cache category tree.

        Args:
            store_id: Store identifier
            tree: Category tree data to cache
        """
        key = self._category_tree_key(store_id)
        await self.cache.set(key, tree, expire=self.TTL_CATEGORY_TREE)

    # =========================================================================
    # Cache Invalidation
    # =========================================================================

    async def invalidate_product(
        self,
        store_id: UUID,
        product_id: UUID,
        category_id: UUID | None = None,
    ) -> int:
        """Invalidate all caches related to a product.

        Call this when a product is created, updated, or deleted.

        Args:
            store_id: Store identifier
            product_id: Product identifier
            category_id: Product's category (for targeted list invalidation)

        Returns:
            Number of cache keys invalidated
        """
        keys_deleted = 0

        # Delete specific product detail cache
        detail_key = self._product_detail_key(store_id, product_id)
        if await self.cache.delete(detail_key):
            keys_deleted += 1

        # Delete all product list caches for this store
        # Using pattern matching to clear all pages/filters
        pattern = f"{self.PREFIX}:products:store:{store_id}:*"
        keys_deleted += await self.cache.clear_pattern(pattern)

        if keys_deleted:
            record_cache_invalidate("product", reason="product_mutation")

        return keys_deleted

    async def invalidate_store_products(self, store_id: UUID) -> int:
        """Invalidate all product caches for a store.

        Call this for bulk operations or store-wide changes.

        Args:
            store_id: Store identifier

        Returns:
            Number of cache keys invalidated
        """
        pattern = f"{self.PREFIX}:products:store:{store_id}:*"
        count = await self.cache.clear_pattern(pattern)
        if count:
            record_cache_invalidate("product", reason="store_bulk")
        return count

    async def invalidate_categories(self, store_id: UUID) -> int:
        """Invalidate category caches for a store.

        Call this when categories are modified.

        Args:
            store_id: Store identifier

        Returns:
            Number of cache keys invalidated
        """
        pattern = f"{self.PREFIX}:categories:store:{store_id}:*"
        return await self.cache.clear_pattern(pattern)

    # =========================================================================
    # Cache Key Generation
    # =========================================================================

    def _products_key(
        self,
        store_id: UUID,
        category_id: UUID | None,
        page: int,
        limit: int,
        filters: dict | None = None,
    ) -> str:
        """Generate cache key for product list.

        Args:
            store_id: Store identifier
            category_id: Optional category filter
            page: Page number
            limit: Items per page
            filters: Additional filter parameters

        Returns:
            Cache key string
        """
        cat = str(category_id) if category_id else "all"
        filter_hash = self._hash_filters(filters) if filters else "none"
        return f"{self.PREFIX}:products:store:{store_id}:cat:{cat}:f:{filter_hash}:p:{page}:l:{limit}"

    def _product_detail_key(self, store_id: UUID, product_id: UUID) -> str:
        """Generate cache key for product detail.

        Args:
            store_id: Store identifier
            product_id: Product identifier

        Returns:
            Cache key string
        """
        return f"{self.PREFIX}:products:store:{store_id}:detail:{product_id}"

    def _category_tree_key(self, store_id: UUID) -> str:
        """Generate cache key for category tree.

        Args:
            store_id: Store identifier

        Returns:
            Cache key string
        """
        return f"{self.PREFIX}:categories:store:{store_id}:tree"

    @staticmethod
    def _hash_filters(filters: dict[str, Any]) -> str:
        """Generate deterministic hash for filter parameters.

        Args:
            filters: Dictionary of filter parameters

        Returns:
            12-character hash string
        """
        # Sort keys for consistent hashing
        sorted_filters = json.dumps(filters, sort_keys=True, default=str)
        return hashlib.sha256(sorted_filters.encode()).hexdigest()[:12]


# Singleton instance for dependency injection
_product_cache_instance: ProductCacheService | None = None


def get_product_cache() -> ProductCacheService:
    """Get or create ProductCacheService singleton.

    Returns:
        ProductCacheService instance
    """
    global _product_cache_instance
    if _product_cache_instance is None:
        _product_cache_instance = ProductCacheService(RedisCacheService())
    return _product_cache_instance

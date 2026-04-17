"""Catalog mapping repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.catalog_mapping import CatalogMapping, CatalogSyncStatus
from src.core.interfaces.repositories.base import BaseRepository


class CatalogMappingRepository(BaseRepository[CatalogMapping]):
    """Repository interface for catalog product mappings."""

    @abstractmethod
    async def get_by_product_and_connection(
        self,
        product_id: UUID,
        channel_connection_id: UUID,
    ) -> CatalogMapping | None:
        """Get mapping by product and connection."""
        ...

    @abstractmethod
    async def get_by_external_product(
        self,
        channel_connection_id: UUID,
        external_product_id: str,
    ) -> CatalogMapping | None:
        """Get mapping by external product ID."""
        ...

    @abstractmethod
    async def list_by_connection(
        self,
        channel_connection_id: UUID,
        sync_status: CatalogSyncStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CatalogMapping]:
        """List mappings for a connection with optional status filter."""
        ...

    @abstractmethod
    async def list_pending(self, limit: int = 100) -> list[CatalogMapping]:
        """List all pending mappings (for incremental sync)."""
        ...

    @abstractmethod
    async def update_sync_status(
        self,
        mapping_id: UUID,
        sync_status: CatalogSyncStatus,
        error: str | None = None,
    ) -> CatalogMapping | None:
        """Update sync status."""
        ...

    @abstractmethod
    async def list_by_store(
        self,
        store_id: UUID,
        sync_status: CatalogSyncStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CatalogMapping]:
        """List mappings for a store."""
        ...

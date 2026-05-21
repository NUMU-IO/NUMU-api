"""Catalog mapping entity."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from .base import BaseEntity


class CatalogSyncStatus(StrEnum):
    """Status of catalog sync for a product."""

    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"
    REMOVED = "removed"


class CatalogMapping(BaseEntity):
    """Maps a local product to a Meta Commerce catalog item."""

    tenant_id: UUID
    store_id: UUID
    product_id: UUID
    channel_connection_id: UUID
    external_catalog_id: str | None = None
    external_product_id: str | None = None
    sync_status: CatalogSyncStatus = CatalogSyncStatus.PENDING
    last_synced_at: datetime | None = None
    last_error: str | None = None

    def mark_synced(self) -> None:
        """Mark product as successfully synced."""
        self.sync_status = CatalogSyncStatus.SYNCED
        self.last_synced_at = datetime.now(UTC)
        self.last_error = None
        self.touch()

    def mark_failed(self, error: str) -> None:
        """Mark product sync as failed with error."""
        self.sync_status = CatalogSyncStatus.FAILED
        self.last_error = error
        self.touch()

    def mark_pending(self) -> None:
        """Mark product for re-sync."""
        self.sync_status = CatalogSyncStatus.PENDING
        self.touch()

    def mark_removed(self) -> None:
        """Mark product as removed from catalog."""
        self.sync_status = CatalogSyncStatus.REMOVED
        self.touch()

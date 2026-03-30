"""Shipment repository interface."""

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.shipment import Shipment
from src.core.interfaces.repositories.base import BaseRepository


class IShipmentRepository(BaseRepository[Shipment]):
    """Shipment repository interface."""

    @abstractmethod
    async def get_by_order(self, order_id: UUID) -> list[Shipment]:
        """Get all shipments for an order."""
        ...

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
        carrier: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        has_cod: bool | None = None,
    ) -> list[Shipment]:
        """Get shipments for a store with optional filters."""
        ...

    @abstractmethod
    async def get_by_tracking_number(self, tracking_number: str) -> Shipment | None:
        """Get shipment by tracking number (cross-store)."""
        ...

    @abstractmethod
    async def get_by_tracking_number_for_update(
        self, tracking_number: str
    ) -> Shipment | None:
        """Get shipment by tracking number with row-level lock."""
        ...

    @abstractmethod
    async def get_by_carrier_shipment_id(
        self, carrier_shipment_id: str
    ) -> Shipment | None:
        """Get shipment by carrier's internal ID."""
        ...

    @abstractmethod
    async def count_by_store(
        self,
        store_id: UUID,
        status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        """Count shipments for a store with optional filters."""
        ...

    @abstractmethod
    async def get_stats(self, store_id: UUID) -> dict:
        """Get aggregated shipment stats for dashboard."""
        ...

    @abstractmethod
    async def get_cod_summary(
        self,
        store_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict:
        """Get COD collection summary."""
        ...

    @abstractmethod
    async def get_active_shipments(
        self, store_id: UUID | None = None
    ) -> list[Shipment]:
        """Get shipments in non-terminal statuses (for background sync)."""
        ...

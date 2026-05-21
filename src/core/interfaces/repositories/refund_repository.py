"""Refund repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.refund import Refund, RefundStatus
from src.core.interfaces.repositories.base import BaseRepository


class IRefundRepository(BaseRepository[Refund]):
    """Refund repository interface."""

    @abstractmethod
    async def get_by_order(
        self,
        order_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Refund]:
        """Get all refunds for an order."""
        ...

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        status: RefundStatus | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Refund]:
        """Get all refunds for a store."""
        ...

    @abstractmethod
    async def count_by_store(
        self,
        store_id: UUID,
        status: RefundStatus | None = None,
    ) -> int:
        """Get total count of refunds for a store."""
        ...

    @abstractmethod
    async def count_by_order(self, order_id: UUID) -> int:
        """Get total count of refunds for an order."""
        ...

    @abstractmethod
    async def get_total_refunded_for_order(self, order_id: UUID) -> int:
        """Get total refunded amount (completed refunds) for an order in cents."""
        ...

    @abstractmethod
    async def get_next_refund_number(self, store_id: UUID) -> str:
        """Generate next refund number for a store."""
        ...

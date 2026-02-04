"""Order repository interface."""

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.order import Order, OrderStatus
from src.core.interfaces.repositories.base import BaseRepository


class IOrderRepository(BaseRepository[Order]):
    """Order repository interface."""

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: OrderStatus | None = None,
    ) -> list[Order]:
        """Get all orders for a store."""
        ...

    @abstractmethod
    async def get_by_customer(
        self,
        customer_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """Get all orders for a customer."""
        ...

    @abstractmethod
    async def get_by_order_number(
        self,
        store_id: UUID,
        order_number: str,
    ) -> Order | None:
        """Get order by order number within a store."""
        ...

    @abstractmethod
    async def get_by_payment_id(self, payment_id: str) -> Order | None:
        """Get order by external payment ID."""
        ...

    @abstractmethod
    async def get_by_date_range(
        self,
        store_id: UUID,
        start_date: datetime,
        end_date: datetime,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """Get orders within a date range."""
        ...

    @abstractmethod
    async def count_by_store(
        self,
        store_id: UUID,
        status: OrderStatus | None = None,
    ) -> int:
        """Get total count of orders for a store."""
        ...

    @abstractmethod
    async def get_revenue_by_date_range(
        self,
        store_id: UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> int:
        """Get total revenue for a date range (in cents)."""
        ...

    @abstractmethod
    async def get_next_order_number(self, store_id: UUID) -> str:
        """Generate next order number for a store."""
        ...

    @abstractmethod
    async def count_by_customer(self, customer_id: UUID) -> int:
        """Get total count of orders for a customer."""
        ...

    @abstractmethod
    async def search(
        self,
        store_id: UUID,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """Search orders by order number or customer notes."""
        ...

"""Coupon repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.coupon import Coupon
from src.core.interfaces.repositories.base import BaseRepository


class ICouponRepository(BaseRepository[Coupon]):
    """Coupon repository interface."""

    @abstractmethod
    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        is_active: bool | None = None,
    ) -> list[Coupon]:
        """Get all coupons for a store."""
        ...

    @abstractmethod
    async def get_by_code(self, store_id: UUID, code: str) -> Coupon | None:
        """Get coupon by code within a store."""
        ...

    @abstractmethod
    async def count_by_store(self, store_id: UUID) -> int:
        """Get total count of coupons for a store."""
        ...

    @abstractmethod
    async def increment_usage(self, coupon_id: UUID) -> None:
        """Atomically increment the coupon usage count."""
        ...

    @abstractmethod
    async def get_customer_usage_count(self, coupon_id: UUID, customer_id: UUID) -> int:
        """Get how many times a customer has used a specific coupon."""
        ...

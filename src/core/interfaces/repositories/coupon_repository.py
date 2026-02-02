"""Coupon repository interface."""

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.coupon import Coupon
from src.core.interfaces.repositories.base import BaseRepository


class ICouponRepository(BaseRepository[Coupon]):
    """Coupon repository interface."""

    @abstractmethod
    async def get_by_code(self, store_id: UUID, code: str) -> Coupon | None:
        """Get coupon by code within a store."""
        ...

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
    async def count_by_store(self, store_id: UUID) -> int:
        """Get total count of coupons for a store."""
        ...

    @abstractmethod
    async def get_active_by_store(
        self,
        store_id: UUID,
        now: datetime | None = None,
    ) -> list[Coupon]:
        """Get currently active and valid coupons for a store."""
        ...

    @abstractmethod
    async def list_with_filters(
        self,
        store_id: UUID | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Coupon]:
        """List coupons with multiple optional filters."""
        ...

    @abstractmethod
    async def count_with_filters(
        self,
        store_id: UUID | None = None,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> int:
        """Count coupons matching the given filters."""
        ...

    @abstractmethod
    async def increment_usage(self, coupon_id: UUID) -> None:
        """Atomically increment the usage count of a coupon."""
        ...

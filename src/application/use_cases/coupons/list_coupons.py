"""List coupons use case."""

from uuid import UUID

from src.application.dto.base import PaginatedDTO
from src.application.dto.coupon import CouponDTO
from src.core.interfaces.repositories.coupon_repository import ICouponRepository


class ListCouponsUseCase:
    """Use case for listing coupons."""

    def __init__(self, coupon_repository: ICouponRepository) -> None:
        self.coupon_repository = coupon_repository

    async def execute(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 20,
        is_active: bool | None = None,
    ) -> PaginatedDTO:
        """List coupons for a store with pagination.

        Args:
            store_id: The store UUID.
            skip: Number of records to skip.
            limit: Maximum number of records to return.
            is_active: Optional filter for active/inactive coupons.

        Returns:
            PaginatedDTO containing coupon data and pagination metadata.
        """
        coupons = await self.coupon_repository.get_by_store(
            store_id=store_id,
            skip=skip,
            limit=limit,
            is_active=is_active,
        )
        total = await self.coupon_repository.count_by_store(store_id)
        page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedDTO.create(
            items=[CouponDTO.from_entity(coupon) for coupon in coupons],
            total=total,
            page=page,
            page_size=limit,
        )

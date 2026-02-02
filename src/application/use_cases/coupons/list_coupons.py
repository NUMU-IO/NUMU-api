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
        store_id: UUID | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> PaginatedDTO:
        """List coupons with filtering and pagination.

        Args:
            store_id: Optional filter by store.
            is_active: Optional filter for active/inactive coupons.
            search: Optional search query for coupon code.
            skip: Number of records to skip.
            limit: Maximum number of records to return.

        Returns:
            PaginatedDTO containing coupon data and pagination metadata.
        """
        coupons = await self.coupon_repository.list_with_filters(
            store_id=store_id,
            is_active=is_active,
            search=search,
            skip=skip,
            limit=limit,
        )

        total = await self.coupon_repository.count_with_filters(
            store_id=store_id,
            is_active=is_active,
            search=search,
        )

        page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedDTO.create(
            items=[CouponDTO.from_entity(c) for c in coupons],
            total=total,
            page=page,
            page_size=limit,
        )

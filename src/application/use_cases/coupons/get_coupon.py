"""Get coupon use case."""

from uuid import UUID

from src.application.dto.coupon import CouponDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository


class GetCouponUseCase:
    """Use case for getting a coupon."""

    def __init__(self, coupon_repository: ICouponRepository) -> None:
        self.coupon_repository = coupon_repository

    async def execute(self, coupon_id: UUID) -> CouponDTO:
        """Get a coupon by ID."""
        coupon = await self.coupon_repository.get_by_id(coupon_id)
        if not coupon:
            raise EntityNotFoundError("Coupon", str(coupon_id))
        return CouponDTO.from_entity(coupon)

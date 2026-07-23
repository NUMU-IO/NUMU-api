"""Get coupon use case."""

from uuid import UUID

from src.application.dto.coupon import CouponDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository


class GetCouponUseCase:
    """Use case for getting a coupon."""

    def __init__(self, coupon_repository: ICouponRepository) -> None:
        self.coupon_repository = coupon_repository

    async def execute(self, coupon_id: UUID, store_id: UUID) -> CouponDTO:
        """Get a coupon by ID, scoped to the store that owns it.

        `store_id` is REQUIRED — the authorised path store. Without it this was
        a cross-OWNER read: any authenticated merchant could read any coupon on
        the platform by id (CL-1, verified 2026-07-21). Foreign coupon is
        reported not-found, never forbidden, so ids can't be probed.
        """
        coupon = await self.coupon_repository.get_by_id(coupon_id)
        if not coupon or coupon.store_id != store_id:
            raise EntityNotFoundError("Coupon", str(coupon_id))
        return CouponDTO.from_entity(coupon)

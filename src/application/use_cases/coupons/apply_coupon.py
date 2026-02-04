"""Apply coupon use case."""

from decimal import Decimal
from uuid import UUID

from src.application.dto.coupon import ApplyCouponDTO
from src.core.entities.coupon import CouponType
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository


class ApplyCouponUseCase:
    """Use case for applying a coupon to an order.

    Validates the coupon, calculates the discount, and atomically
    increments the coupon usage count.
    """

    def __init__(self, coupon_repository: ICouponRepository) -> None:
        self.coupon_repository = coupon_repository

    async def execute(
        self,
        store_id: UUID,
        code: str,
        order_amount: Decimal,
        for_update: bool = False,
    ) -> ApplyCouponDTO:
        """Apply a coupon and record its usage.

        Args:
            store_id: The store UUID.
            code: The coupon code.
            order_amount: The order subtotal.
            for_update: If True, lock the coupon row to prevent concurrent usage.

        Returns:
            ApplyCouponDTO with the calculated discount details.

        Raises:
            EntityNotFoundError: If coupon code not found.
            ValidationError: If coupon cannot be applied.
        """
        if for_update and hasattr(self.coupon_repository, "get_by_code_for_update"):
            coupon = await self.coupon_repository.get_by_code_for_update(store_id, code)
        else:
            coupon = await self.coupon_repository.get_by_code(store_id, code)
        if not coupon:
            raise EntityNotFoundError("Coupon", code)

        if not coupon.is_usable:
            raise ValidationError("This coupon cannot be applied")

        if not coupon.meets_minimum_order(order_amount):
            raise ValidationError(
                f"Order total must be at least {coupon.min_order_amount} "
                f"to use this coupon"
            )

        # Calculate discount
        discount_amount = coupon.calculate_discount(order_amount)
        free_shipping = coupon.coupon_type == CouponType.FREE_SHIPPING

        # Record usage atomically
        await self.coupon_repository.increment_usage(coupon.id)

        return ApplyCouponDTO(
            coupon_id=coupon.id,
            code=coupon.code,
            coupon_type=coupon.coupon_type.value,
            discount_amount=discount_amount,
            free_shipping=free_shipping,
        )

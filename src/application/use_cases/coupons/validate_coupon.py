"""Validate coupon use case."""

from decimal import Decimal
from uuid import UUID

from src.application.dto.coupon import CouponDTO
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository


class ValidateCouponUseCase:
    """Use case for validating a coupon code against an order.

    Checks whether a coupon exists, is active, within its validity
    window, has remaining uses, and meets the minimum order amount.
    """

    def __init__(self, coupon_repository: ICouponRepository) -> None:
        self.coupon_repository = coupon_repository

    async def execute(
        self,
        store_id: UUID,
        code: str,
        order_amount: Decimal | None = None,
    ) -> CouponDTO:
        """Validate a coupon code.

        Args:
            store_id: The store UUID.
            code: The coupon code to validate.
            order_amount: Optional order subtotal to check minimum order requirement.

        Returns:
            CouponDTO if the coupon is valid.

        Raises:
            EntityNotFoundError: If coupon code not found in the store.
            ValidationError: If coupon is not usable or order doesn't meet minimum.
        """
        coupon = await self.coupon_repository.get_by_code(store_id, code)
        if not coupon:
            raise EntityNotFoundError("Coupon", code)

        if not coupon.is_active:
            raise ValidationError("This coupon is currently inactive")

        if coupon.is_expired:
            raise ValidationError("This coupon has expired")

        if not coupon.is_started:
            raise ValidationError("This coupon is not yet valid")

        if not coupon.has_remaining_uses:
            raise ValidationError("This coupon has reached its usage limit")

        if order_amount is not None and not coupon.meets_minimum_order(order_amount):
            raise ValidationError(
                f"Order total must be at least {coupon.min_order_amount} "
                f"to use this coupon"
            )

        return CouponDTO.from_entity(coupon)

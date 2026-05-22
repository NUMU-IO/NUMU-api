"""Apply coupon use case."""

from decimal import Decimal
from typing import Any
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
        line_items: list[dict[str, Any]] | None = None,
    ) -> ApplyCouponDTO:
        """Apply a coupon and record its usage.

        Args:
            store_id: The store UUID.
            code: The coupon code.
            order_amount: The order subtotal.
            for_update: If True, lock the coupon row to prevent concurrent usage.
            line_items: Optional cart line items (each with product_id /
                unit_price / quantity). Required for BUY_X_GET_Y
                coupons (Phase 8.4); ignored for simpler types.

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
            raise EntityNotFoundError("Coupon", code, identifier_name="code")

        if not coupon.is_usable:
            raise ValidationError("This coupon cannot be applied")

        if not coupon.meets_minimum_order(order_amount):
            raise ValidationError(
                f"Order total must be at least {coupon.min_order_amount} "
                f"to use this coupon"
            )

        # Calculate discount — line_items is only consulted for
        # BUY_X_GET_Y; simple types pass through identically.
        discount_amount = coupon.calculate_discount(order_amount, line_items=line_items)
        free_shipping = coupon.coupon_type == CouponType.FREE_SHIPPING

        # Record usage atomically
        await self.coupon_repository.increment_usage(coupon.id)

        return ApplyCouponDTO(
            coupon_id=coupon.id,
            code=coupon.code,
            coupon_type=coupon.coupon_type.value,
            discount_amount=discount_amount,
            free_shipping=free_shipping,
            # Surfaces the campaign FK so the checkout can attribute
            # the resulting order to the campaign when no UTM-resolved
            # campaign won first. Standalone coupons carry None.
            campaign_id=coupon.campaign_id,
        )

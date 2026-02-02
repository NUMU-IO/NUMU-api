"""Apply coupon use case — validates and calculates discount."""

from datetime import datetime, timezone
from uuid import UUID

from src.application.dto.coupon import ApplyCouponResultDTO
from src.core.exceptions import EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository


class ApplyCouponUseCase:
    """Use case for validating and applying a coupon code."""

    def __init__(self, coupon_repository: ICouponRepository) -> None:
        self.coupon_repository = coupon_repository

    async def execute(
        self,
        store_id: UUID,
        coupon_code: str,
        subtotal: int,
        customer_id: UUID | None = None,
    ) -> ApplyCouponResultDTO:
        """Validate a coupon and calculate the discount.

        Args:
            store_id: The store UUID.
            coupon_code: The coupon code to validate.
            subtotal: Order subtotal in cents.
            customer_id: Optional customer UUID for per-customer limit checks.

        Returns:
            ApplyCouponResultDTO with discount details.

        Raises:
            EntityNotFoundError: If coupon code not found.
            ValidationError: If coupon is invalid for this order.
        """
        coupon = await self.coupon_repository.get_by_code(store_id, coupon_code)
        if not coupon:
            raise EntityNotFoundError("Coupon", coupon_code)

        # Check per-customer usage
        customer_usage = 0
        if customer_id and coupon.max_uses_per_customer is not None:
            customer_usage = await self.coupon_repository.get_customer_usage_count(
                coupon.id, customer_id
            )

        # Validate coupon
        now = datetime.now(timezone.utc)
        is_valid, error_message = coupon.is_valid(
            subtotal=subtotal,
            now=now,
            customer_usage_count=customer_usage,
        )
        if not is_valid:
            raise ValidationError(error_message or "Coupon is not valid")

        # Calculate discount
        calculated_discount = coupon.calculate_discount(subtotal)

        return ApplyCouponResultDTO(
            coupon_id=coupon.id,
            coupon_code=coupon.code,
            discount_type=coupon.discount_type.value,
            discount_value=coupon.discount_value,
            calculated_discount=calculated_discount,
            message=f"Coupon '{coupon.code}' applied successfully",
        )

"""Update coupon use case."""

from uuid import UUID

from src.application.dto.coupon import CouponDTO, UpdateCouponDTO
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class UpdateCouponUseCase:
    """Use case for updating a coupon."""

    def __init__(
        self,
        coupon_repository: ICouponRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.coupon_repository = coupon_repository
        self.store_repository = store_repository

    async def execute(
        self,
        coupon_id: UUID,
        dto: UpdateCouponDTO,
        user_id: UUID,
    ) -> CouponDTO:
        """Update an existing coupon.

        Args:
            coupon_id: The coupon UUID.
            dto: The update coupon DTO.
            user_id: The user UUID (for authorization).

        Returns:
            CouponDTO with updated coupon data.

        Raises:
            EntityNotFoundError: If coupon or store not found.
            AuthorizationError: If user doesn't own the store.
            ValidationError: If data is invalid.
        """
        coupon = await self.coupon_repository.get_by_id(coupon_id)
        if not coupon:
            raise EntityNotFoundError("Coupon", str(coupon_id))

        # Verify store ownership
        store = await self.store_repository.get_by_id(coupon.store_id)
        if not store or store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to update this coupon"
            )

        # Apply non-None fields
        if dto.description is not None:
            coupon.description = dto.description
        if dto.discount_value is not None:
            if coupon.discount_type.value == "percentage" and dto.discount_value > 100:
                raise ValidationError("Percentage discount cannot exceed 100")
            coupon.discount_value = dto.discount_value
        if dto.min_order_amount is not None:
            coupon.min_order_amount = dto.min_order_amount
        if dto.max_discount_amount is not None:
            coupon.max_discount_amount = dto.max_discount_amount
        if dto.max_uses is not None:
            coupon.max_uses = dto.max_uses
        if dto.max_uses_per_customer is not None:
            coupon.max_uses_per_customer = dto.max_uses_per_customer
        if dto.valid_from is not None:
            coupon.valid_from = dto.valid_from
        if dto.valid_to is not None:
            coupon.valid_to = dto.valid_to
        if dto.is_active is not None:
            coupon.is_active = dto.is_active

        # Validate date range if both set
        if coupon.valid_from and coupon.valid_to and coupon.valid_from >= coupon.valid_to:
            raise ValidationError("valid_from must be before valid_to")

        coupon.touch()
        updated_coupon = await self.coupon_repository.update(coupon)
        return CouponDTO.from_entity(updated_coupon)

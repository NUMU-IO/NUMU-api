"""Update coupon use case."""

from uuid import UUID

from src.application.dto.coupon import CouponDTO, UpdateCouponDTO
from src.core.entities.coupon import CouponType
from src.core.exceptions import (
    AuthorizationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ValidationError,
)
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
        """Update a coupon.

        Args:
            coupon_id: The coupon UUID.
            dto: The update coupon DTO.
            user_id: The user UUID (for authorization).

        Returns:
            CouponDTO with updated coupon data.

        Raises:
            EntityNotFoundError: If coupon not found.
            AuthorizationError: If user doesn't own the store.
            EntityAlreadyExistsError: If new code already exists.
            ValidationError: If updated data is invalid.
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

        # Update fields
        if dto.code is not None:
            new_code = dto.code.strip().upper()
            if new_code != coupon.code:
                existing = await self.coupon_repository.get_by_code(
                    coupon.store_id, new_code
                )
                if existing:
                    raise EntityAlreadyExistsError("Coupon", "code", new_code)
                coupon.code = new_code

        if dto.coupon_type is not None:
            try:
                coupon.coupon_type = CouponType(dto.coupon_type)
            except ValueError:
                valid = [t.value for t in CouponType]
                raise ValidationError(
                    f"Invalid coupon type. Valid types: {', '.join(valid)}"
                )

        if dto.value is not None:
            coupon.value = dto.value
        if dto.min_order_amount is not None:
            coupon.min_order_amount = dto.min_order_amount
        if dto.max_discount_amount is not None:
            coupon.max_discount_amount = dto.max_discount_amount
        if dto.usage_limit is not None:
            coupon.usage_limit = dto.usage_limit
        if dto.valid_from is not None:
            coupon.valid_from = dto.valid_from
        if dto.valid_until is not None:
            coupon.valid_until = dto.valid_until
        if dto.is_active is not None:
            coupon.is_active = dto.is_active

        coupon.touch()
        updated = await self.coupon_repository.update(coupon)
        return CouponDTO.from_entity(updated)

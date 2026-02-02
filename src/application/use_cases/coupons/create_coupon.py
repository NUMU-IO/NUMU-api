"""Create coupon use case."""

from uuid import UUID

from src.application.dto.coupon import CouponDTO, CreateCouponDTO
from src.core.entities.coupon import Coupon, DiscountType
from src.core.exceptions import AuthorizationError, EntityAlreadyExistsError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class CreateCouponUseCase:
    """Use case for creating a new coupon."""

    def __init__(
        self,
        coupon_repository: ICouponRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.coupon_repository = coupon_repository
        self.store_repository = store_repository

    async def execute(
        self,
        dto: CreateCouponDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> CouponDTO:
        """Create a new coupon.

        Args:
            dto: The create coupon DTO.
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            CouponDTO with created coupon data.

        Raises:
            EntityNotFoundError: If store not found.
            AuthorizationError: If user doesn't own the store.
            EntityAlreadyExistsError: If coupon code already exists.
            ValidationError: If coupon data is invalid.
        """
        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to create coupons for this store"
            )

        # Validate discount type
        try:
            discount_type = DiscountType(dto.discount_type)
        except ValueError:
            raise ValidationError(
                f"Invalid discount type '{dto.discount_type}'. "
                f"Valid types: {', '.join(t.value for t in DiscountType)}"
            )

        # Validate percentage range
        if discount_type == DiscountType.PERCENTAGE and dto.discount_value > 100:
            raise ValidationError("Percentage discount cannot exceed 100")

        # Validate date range
        if dto.valid_from and dto.valid_to and dto.valid_from >= dto.valid_to:
            raise ValidationError("valid_from must be before valid_to")

        # Normalize code to uppercase
        code = dto.code.strip().upper()
        if not code:
            raise ValidationError("Coupon code is required")

        # Check uniqueness within store
        existing = await self.coupon_repository.get_by_code(store_id, code)
        if existing:
            raise EntityAlreadyExistsError("Coupon", "code", code)

        # Create coupon entity
        coupon = Coupon(
            store_id=store_id,
            code=code,
            description=dto.description,
            discount_type=discount_type,
            discount_value=dto.discount_value,
            min_order_amount=dto.min_order_amount,
            max_discount_amount=dto.max_discount_amount,
            max_uses=dto.max_uses,
            max_uses_per_customer=dto.max_uses_per_customer,
            valid_from=dto.valid_from,
            valid_to=dto.valid_to,
            is_active=dto.is_active,
        )

        created_coupon = await self.coupon_repository.create(coupon)
        return CouponDTO.from_entity(created_coupon)

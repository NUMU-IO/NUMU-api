"""Create coupon use case."""

from uuid import UUID

from src.application.dto.coupon import CouponDTO, CreateCouponDTO
from src.core.entities.coupon import Coupon, CouponType
from src.core.exceptions import (
    AuthorizationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository

VALID_COUPON_TYPES = {t.value for t in CouponType}


class CreateCouponUseCase:
    """Use case for creating a new coupon."""

    def __init__(
        self,
        coupon_repository: ICouponRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.coupon_repository = coupon_repository
        self.store_repository = store_repository

    def _validate(self, dto: CreateCouponDTO) -> list[str]:
        """Validate coupon data and return list of errors."""
        errors: list[str] = []

        if not dto.code or not dto.code.strip():
            errors.append("Coupon code is required")

        if dto.coupon_type not in VALID_COUPON_TYPES:
            errors.append(
                f"Invalid coupon type. Valid types: {', '.join(VALID_COUPON_TYPES)}"
            )

        if dto.coupon_type == CouponType.PERCENTAGE.value:
            if dto.value <= 0 or dto.value > 100:
                errors.append("Percentage value must be between 0 and 100")
        elif dto.coupon_type == CouponType.FIXED.value:
            if dto.value <= 0:
                errors.append("Fixed discount value must be greater than 0")

        if dto.min_order_amount is not None and dto.min_order_amount < 0:
            errors.append("Minimum order amount cannot be negative")

        if dto.max_discount_amount is not None and dto.max_discount_amount <= 0:
            errors.append("Maximum discount amount must be greater than 0")

        if dto.usage_limit is not None and dto.usage_limit < 1:
            errors.append("Usage limit must be at least 1")

        if dto.valid_from and dto.valid_until and dto.valid_from >= dto.valid_until:
            errors.append("valid_from must be before valid_until")

        return errors

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
            EntityAlreadyExistsError: If coupon code already exists in store.
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

        # Validate coupon data
        validation_errors = self._validate(dto)
        if validation_errors:
            raise ValidationError(
                "Coupon validation failed:\n• " + "\n• ".join(validation_errors)
            )

        # Check code uniqueness within the store
        code = dto.code.strip().upper()
        existing = await self.coupon_repository.get_by_code(store_id, code)
        if existing:
            raise EntityAlreadyExistsError("Coupon", "code", code)

        # Parse coupon type
        coupon_type = CouponType(dto.coupon_type)

        # Create entity
        coupon = Coupon(
            store_id=store_id,
            code=code,
            coupon_type=coupon_type,
            value=dto.value,
            min_order_amount=dto.min_order_amount,
            max_discount_amount=dto.max_discount_amount,
            usage_limit=dto.usage_limit,
            valid_from=dto.valid_from,
            valid_until=dto.valid_until,
        )

        created = await self.coupon_repository.create(coupon)
        return CouponDTO.from_entity(created)

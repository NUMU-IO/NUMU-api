"""Coupon DTOs."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.coupon import Coupon


@dataclass
class CouponDTO(BaseDTO):
    """Coupon data transfer object."""

    id: UUID
    store_id: UUID
    code: str
    description: str | None
    discount_type: str
    discount_value: int
    min_order_amount: int
    max_discount_amount: int | None
    max_uses: int | None
    max_uses_per_customer: int | None
    current_usage_count: int
    valid_from: datetime | None
    valid_to: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, entity: Coupon) -> "CouponDTO":
        """Create DTO from domain entity."""
        return cls(
            id=entity.id,
            store_id=entity.store_id,
            code=entity.code,
            description=entity.description,
            discount_type=entity.discount_type.value,
            discount_value=entity.discount_value,
            min_order_amount=entity.min_order_amount,
            max_discount_amount=entity.max_discount_amount,
            max_uses=entity.max_uses,
            max_uses_per_customer=entity.max_uses_per_customer,
            current_usage_count=entity.current_usage_count,
            valid_from=entity.valid_from,
            valid_to=entity.valid_to,
            is_active=entity.is_active,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CreateCouponDTO(BaseDTO):
    """DTO for creating a coupon."""

    code: str
    discount_type: str
    discount_value: int
    description: str | None = None
    min_order_amount: int = 0
    max_discount_amount: int | None = None
    max_uses: int | None = None
    max_uses_per_customer: int | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_active: bool = True


@dataclass
class UpdateCouponDTO(BaseDTO):
    """DTO for updating a coupon."""

    description: str | None = None
    discount_value: int | None = None
    min_order_amount: int | None = None
    max_discount_amount: int | None = None
    max_uses: int | None = None
    max_uses_per_customer: int | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_active: bool | None = None


@dataclass
class ApplyCouponResultDTO(BaseDTO):
    """Result of applying a coupon."""

    coupon_id: UUID
    coupon_code: str
    discount_type: str
    discount_value: int
    calculated_discount: int
    message: str

"""Coupon DTOs."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.coupon import Coupon


@dataclass
class CouponDTO(BaseDTO):
    """Coupon data transfer object."""

    id: UUID
    store_id: UUID
    code: str
    coupon_type: str
    value: Decimal
    min_order_amount: Decimal | None
    max_discount_amount: Decimal | None
    usage_limit: int | None
    usage_count: int
    valid_from: datetime | None
    valid_until: datetime | None
    is_active: bool
    is_expired: bool
    is_usable: bool
    applicable_product_ids: list[UUID] | None
    applicable_category_ids: list[UUID] | None
    config: dict | None = None
    created_at: datetime = None
    updated_at: datetime = None

    @classmethod
    def from_entity(cls, entity: Coupon) -> "CouponDTO":
        """Create DTO from Coupon entity."""
        return cls(
            id=entity.id,
            store_id=entity.store_id,
            code=entity.code,
            coupon_type=entity.coupon_type.value,
            value=entity.value,
            min_order_amount=entity.min_order_amount,
            max_discount_amount=entity.max_discount_amount,
            usage_limit=entity.usage_limit,
            usage_count=entity.usage_count,
            valid_from=entity.valid_from,
            valid_until=entity.valid_until,
            is_active=entity.is_active,
            is_expired=entity.is_expired,
            is_usable=entity.is_usable,
            applicable_product_ids=entity.applicable_product_ids,
            applicable_category_ids=entity.applicable_category_ids,
            config=entity.config,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CreateCouponDTO(BaseDTO):
    """Create coupon data transfer object."""

    code: str
    coupon_type: str
    value: Decimal = Decimal("0")
    min_order_amount: Decimal | None = None
    max_discount_amount: Decimal | None = None
    usage_limit: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    applicable_product_ids: list[UUID] | None = None
    applicable_category_ids: list[UUID] | None = None
    # Phase 8.4 — type-specific config for BUY_X_GET_Y + TIERED.
    config: dict | None = None


@dataclass
class UpdateCouponDTO(BaseDTO):
    """Update coupon data transfer object."""

    code: str | None = None
    coupon_type: str | None = None
    value: Decimal | None = None
    min_order_amount: Decimal | None = None
    max_discount_amount: Decimal | None = None
    usage_limit: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_active: bool | None = None
    applicable_product_ids: list[UUID] | None = None
    applicable_category_ids: list[UUID] | None = None
    # Phase 8.4 — replace type-specific config (None = no change).
    config: dict | None = None


@dataclass
class ApplyCouponDTO(BaseDTO):
    """Result of applying a coupon to an order."""

    coupon_id: UUID
    code: str
    coupon_type: str
    discount_amount: Decimal
    free_shipping: bool
    # Set when the coupon was issued under a marketing campaign — the
    # checkout uses this as a fallback campaign_id when no UTM-resolved
    # attribution wins. None for standalone coupons (the normal case).
    campaign_id: UUID | None = None

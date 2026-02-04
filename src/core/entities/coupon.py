"""Coupon entity for discount codes."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import Field, field_validator

from src.core.entities.base import BaseEntity


class CouponType(StrEnum):
    """Coupon discount type enumeration."""

    PERCENTAGE = "percentage"
    FIXED = "fixed"
    FREE_SHIPPING = "free_shipping"


class Coupon(BaseEntity):
    """Coupon entity representing a discount code for a store.

    Coupons can offer percentage discounts, fixed-amount discounts,
    or free shipping. They support usage limits and validity date ranges.
    """

    store_id: UUID
    code: str = Field(..., min_length=1, max_length=50)
    coupon_type: CouponType
    value: Decimal = Field(default=Decimal("0"), ge=0)
    min_order_amount: Decimal | None = Field(None, ge=0)
    max_discount_amount: Decimal | None = Field(None, ge=0)
    usage_limit: int | None = Field(None, ge=0)
    usage_count: int = Field(default=0, ge=0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_active: bool = True

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        """Normalize coupon code to uppercase and stripped."""
        return v.strip().upper()

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Decimal, info) -> Decimal:
        """Validate value based on coupon type context."""
        if v < 0:
            raise ValueError("Coupon value cannot be negative")
        return v

    @property
    def is_expired(self) -> bool:
        """Check if coupon has passed its validity end date."""
        if self.valid_until is None:
            return False
        return datetime.now(UTC) > self.valid_until

    @property
    def is_started(self) -> bool:
        """Check if coupon validity period has started."""
        if self.valid_from is None:
            return True
        return datetime.now(UTC) >= self.valid_from

    @property
    def is_within_validity(self) -> bool:
        """Check if coupon is within its validity date range."""
        return self.is_started and not self.is_expired

    @property
    def has_remaining_uses(self) -> bool:
        """Check if coupon has remaining uses."""
        if self.usage_limit is None:
            return True
        return self.usage_count < self.usage_limit

    @property
    def is_usable(self) -> bool:
        """Check if coupon can currently be used."""
        return self.is_active and self.is_within_validity and self.has_remaining_uses

    def meets_minimum_order(self, order_amount: Decimal) -> bool:
        """Check if an order amount meets the minimum order requirement.

        Args:
            order_amount: The order subtotal to check against.

        Returns:
            True if the order meets the minimum, or no minimum is set.
        """
        if self.min_order_amount is None:
            return True
        return order_amount >= self.min_order_amount

    def calculate_discount(self, order_amount: Decimal) -> Decimal:
        """Calculate the discount amount for a given order total.

        Args:
            order_amount: The order subtotal to calculate discount for.

        Returns:
            The discount amount (capped by max_discount_amount if set).
        """
        if self.coupon_type == CouponType.FREE_SHIPPING:
            return Decimal("0")

        if self.coupon_type == CouponType.PERCENTAGE:
            discount = order_amount * self.value / Decimal("100")
        else:
            # Fixed discount — cannot exceed the order amount
            discount = min(self.value, order_amount)

        if self.max_discount_amount is not None:
            discount = min(discount, self.max_discount_amount)

        return discount

    def record_usage(self) -> None:
        """Record a single usage of this coupon.

        Raises:
            ValueError: If the coupon has no remaining uses.
        """
        if not self.has_remaining_uses:
            raise ValueError(
                f"Coupon '{self.code}' has reached its usage limit of {self.usage_limit}"
            )
        self.usage_count += 1
        self.touch()

    def deactivate(self) -> None:
        """Deactivate the coupon."""
        self.is_active = False
        self.touch()

    def activate(self) -> None:
        """Activate the coupon."""
        self.is_active = True
        self.touch()

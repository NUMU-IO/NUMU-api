"""Coupon entity representing a discount coupon for a store."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class DiscountType(str, Enum):
    """Coupon discount type enumeration."""

    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class Coupon(BaseEntity):
    """Coupon entity representing a store discount coupon.

    For PERCENTAGE type, discount_value is the integer percentage (e.g. 10 = 10%).
    For FIXED_AMOUNT type, discount_value is in cents.
    """

    store_id: UUID
    code: str
    description: str | None = None
    discount_type: DiscountType
    discount_value: int = Field(ge=0)
    min_order_amount: int = Field(default=0, ge=0)
    max_discount_amount: int | None = None
    max_uses: int | None = None
    max_uses_per_customer: int | None = None
    current_usage_count: int = Field(default=0, ge=0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_valid(
        self,
        subtotal: int,
        now: datetime | None = None,
        customer_usage_count: int = 0,
    ) -> tuple[bool, str | None]:
        """Check if the coupon is valid for the given order.

        Args:
            subtotal: Order subtotal in cents.
            now: Current time (defaults to UTC now).
            customer_usage_count: How many times the customer has used this coupon.

        Returns:
            Tuple of (is_valid, error_message).
        """
        if now is None:
            now = datetime.now(timezone.utc)

        if not self.is_active:
            return False, "Coupon is not active"

        if self.valid_from and now < self.valid_from:
            return False, "Coupon is not yet valid"

        if self.valid_to and now > self.valid_to:
            return False, "Coupon has expired"

        if self.max_uses is not None and self.current_usage_count >= self.max_uses:
            return False, "Coupon usage limit reached"

        if self.max_uses_per_customer is not None and customer_usage_count >= self.max_uses_per_customer:
            return False, "You have already used this coupon the maximum number of times"

        if subtotal < self.min_order_amount:
            return False, f"Minimum order amount not met (required: {self.min_order_amount} cents)"

        return True, None

    def calculate_discount(self, subtotal: int) -> int:
        """Calculate the discount amount for a given subtotal.

        Args:
            subtotal: Order subtotal in cents.

        Returns:
            Discount amount in cents.
        """
        if self.discount_type == DiscountType.PERCENTAGE:
            discount = subtotal * self.discount_value // 100
            if self.max_discount_amount is not None:
                discount = min(discount, self.max_discount_amount)
            return discount
        else:
            # Fixed amount: discount cannot exceed subtotal
            return min(self.discount_value, subtotal)

    def increment_usage(self) -> None:
        """Increment the usage count."""
        self.current_usage_count += 1
        self.touch()

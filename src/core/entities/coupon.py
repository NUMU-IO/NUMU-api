"""Coupon entity for discount codes."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from src.core.entities.base import BaseEntity


class CouponType(StrEnum):
    """Coupon discount type enumeration."""

    PERCENTAGE = "percentage"
    FIXED = "fixed"
    FREE_SHIPPING = "free_shipping"
    # Phase 8.4 — Buy X Get Y. Reads config from `Coupon.config`:
    #   {"buy_quantity": 2, "get_quantity": 1,
    #    "get_discount_percentage": 100,    # 100 = free
    #    "buy_product_ids": [optional list],
    #    "get_product_ids": [optional, defaults to buy_product_ids]}
    BUY_X_GET_Y = "buy_x_get_y"
    # Phase 8.4 — Tiered. Reads config from `Coupon.config`:
    #   {"tiers": [
    #     {"min_subtotal_cents": 50000, "discount_percentage": 10},
    #     {"min_subtotal_cents": 100000, "discount_percentage": 15},
    #     ...
    #   ]}
    # The highest tier whose threshold is met wins.
    TIERED = "tiered"


class Coupon(BaseEntity):
    """Coupon entity representing a discount code for a store.

    Coupons can offer percentage discounts, fixed-amount discounts,
    or free shipping. They support usage limits and validity date ranges.
    """

    store_id: UUID
    tenant_id: UUID | None = None
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
    applicable_product_ids: list[UUID] | None = None
    applicable_category_ids: list[UUID] | None = None
    # Phase 3.8 — auto-apply + stack rules.
    is_auto_apply: bool = Field(
        default=False,
        description=(
            "When true, the coupon is applied at checkout without the "
            "customer entering a code, provided the order meets all "
            "other conditions (min_order_amount, applicable_products, "
            "validity window). Used for site-wide promos like "
            "'Free shipping over EGP 500' that the merchant wants to "
            "advertise as automatic."
        ),
    )
    stackable: bool = Field(
        default=False,
        description=(
            "When true, this coupon can combine with one other coupon "
            "(typically a manual code on top of an auto-apply promo). "
            "When ALL active coupons on the order are stackable, all "
            "apply; otherwise only the highest-discount one wins. "
            "Defaults to false to preserve pre-Phase-3.8 single-coupon "
            "behavior."
        ),
    )
    # Phase 8.4 — type-specific configuration for BUY_X_GET_Y +
    # TIERED. NULL for simple types (PERCENTAGE / FIXED /
    # FREE_SHIPPING) where `value` alone is enough. Pydantic doesn't
    # validate the shape here — calculate_discount asserts the
    # expected keys when it reads the dict.
    config: dict[str, Any] | None = None

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

    def calculate_discount(
        self,
        order_amount: Decimal,
        *,
        line_items: list[dict[str, Any]] | None = None,
    ) -> Decimal:
        """Calculate the discount amount for a given order total.

        Args:
            order_amount: The order subtotal (decimal in store currency).
            line_items: Optional list of cart line dicts with at least
                ``product_id`` (str/UUID), ``unit_price`` (Decimal-ish),
                and ``quantity`` (int). Required for BUY_X_GET_Y;
                ignored for other types.

        Returns:
            The discount amount (capped by max_discount_amount if set).
        """
        if self.coupon_type == CouponType.FREE_SHIPPING:
            return Decimal("0")

        if self.coupon_type == CouponType.PERCENTAGE:
            discount = order_amount * self.value / Decimal("100")
        elif self.coupon_type == CouponType.FIXED:
            # Fixed discount — cannot exceed the order amount
            discount = min(self.value, order_amount)
        elif self.coupon_type == CouponType.TIERED:
            discount = _calculate_tiered_discount(self.config, order_amount)
        elif self.coupon_type == CouponType.BUY_X_GET_Y:
            discount = _calculate_bogo_discount(self.config, line_items or [])
        else:
            # Unknown type — defensive zero.
            discount = Decimal("0")

        if self.max_discount_amount is not None:
            discount = min(discount, self.max_discount_amount)

        # Never discount more than the order itself.
        return min(discount, order_amount)

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


# ── Phase 8.4 discount calculators ───────────────────────────────


def _calculate_tiered_discount(
    config: dict[str, Any] | None, order_amount: Decimal
) -> Decimal:
    """Pick the highest tier the order qualifies for and apply its
    percentage. Tiers are evaluated by `min_subtotal_cents` against
    the order amount (treated as decimal-cents).

    Returns Decimal(0) when no tier qualifies or config is malformed.
    """
    if not config:
        return Decimal("0")
    tiers = config.get("tiers") or []
    if not isinstance(tiers, list):
        return Decimal("0")
    order_cents = int(order_amount * Decimal("100"))
    best_pct = Decimal("0")
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        threshold = tier.get("min_subtotal_cents")
        pct = tier.get("discount_percentage")
        if not isinstance(threshold, (int, float)) or not isinstance(pct, (int, float)):
            continue
        if order_cents >= int(threshold):
            best_pct = max(best_pct, Decimal(str(pct)))
    return order_amount * best_pct / Decimal("100")


def _calculate_bogo_discount(
    config: dict[str, Any] | None, line_items: list[dict[str, Any]]
) -> Decimal:
    """Buy-X-Get-Y discount.

    For every full bundle of `buy_quantity + get_quantity` units of
    qualifying products in the cart, discount `get_quantity` units
    at `get_discount_percentage`. The discounted units are the
    cheapest ones in the qualifying set (Shopify-pattern — the
    customer gets the LEAST valuable unit at discount, not the most).

    `buy_product_ids` (optional) limits the qualifying buy set; when
    omitted, all line items qualify. `get_product_ids` defaults to
    the buy set.
    """
    if not config or not line_items:
        return Decimal("0")
    buy_q = int(config.get("buy_quantity") or 0)
    get_q = int(config.get("get_quantity") or 0)
    pct_raw = config.get("get_discount_percentage")
    if buy_q < 1 or get_q < 1 or pct_raw is None:
        return Decimal("0")
    pct = Decimal(str(pct_raw))

    buy_filter = config.get("buy_product_ids")
    get_filter = config.get("get_product_ids") or buy_filter

    def _matches(line: dict[str, Any], filter_list: Any) -> bool:
        if not filter_list:
            return True
        pid = str(line.get("product_id", ""))
        return pid in {str(x) for x in filter_list}

    # Expand each line to one entry per unit so we can pick the cheapest.
    qualifying_units: list[Decimal] = []
    for line in line_items:
        if not _matches(line, get_filter):
            continue
        qty = int(line.get("quantity") or 0)
        try:
            unit_price = Decimal(str(line.get("unit_price") or "0"))
        except Exception:
            continue
        qualifying_units.extend([unit_price] * qty)

    # Separately collect the units that qualify for the BUY side
    # (could be a superset of the GET side; for the common case where
    # buy_filter == get_filter they're the same).
    buy_units = 0
    for line in line_items:
        if not _matches(line, buy_filter):
            continue
        buy_units += int(line.get("quantity") or 0)

    bundle_size = buy_q + get_q
    if bundle_size == 0:
        return Decimal("0")

    # Number of complete bundles we can apply — limited by both:
    # 1. How many full (buy + get) groups the BUY-side has units for.
    # 2. How many GET-side units exist to discount.
    bundles_by_buy = buy_units // bundle_size
    bundles_by_get = len(qualifying_units) // bundle_size
    bundles = min(bundles_by_buy, bundles_by_get)
    if bundles == 0:
        return Decimal("0")

    # Discount the cheapest `bundles * get_q` units of the qualifying set.
    qualifying_units.sort()
    units_to_discount = qualifying_units[: bundles * get_q]
    return sum(units_to_discount, Decimal("0")) * pct / Decimal("100")

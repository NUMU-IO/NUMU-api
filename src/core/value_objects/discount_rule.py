"""DiscountRule value object — the math for a single discount.

Pure value object: immutable, no I/O, no service dependencies. Given a
`DiscountContext` it returns a `DiscountResult` saying how many cents to
subtract from the subtotal and whether free shipping applies.

The application layer composes multiple `DiscountResult`s via
`services.discount_calculator.DiscountCalculator`.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiscountRuleKind(StrEnum):
    """Kinds of discount math the rule can perform."""

    PERCENTAGE = "percentage"
    FIXED = "fixed"
    FREE_SHIPPING = "free_shipping"
    BOGO = "bogo"
    TIERED = "tiered"


class DiscountTier(BaseModel):
    """One step of a tiered discount: 'spend X, get Y%'."""

    model_config = ConfigDict(frozen=True)

    threshold_cents: int = Field(ge=0)
    percent: int = Field(ge=0, le=100)


@dataclass(frozen=True)
class CartLine:
    """Slim view of a cart line for discount math."""

    product_id: UUID
    quantity: int
    unit_price_cents: int
    category_id: UUID | None = None


@dataclass(frozen=True)
class DiscountContext:
    """Inputs the calculator needs to evaluate a rule.

    Pure data — never reaches into a repository or session.
    """

    subtotal_cents: int
    line_items: list[CartLine]
    shipping_cents: int = 0
    customer_id: UUID | None = None


@dataclass(frozen=True)
class DiscountResult:
    """Outcome of one rule evaluation."""

    discount_cents: int  # >= 0 — caller floors at subtotal
    free_shipping: bool = False
    affected_line_item_ids: list[UUID] = field(default_factory=list)
    explanation: str = ""


class DiscountRule(BaseModel):
    """Frozen value object — the math for one discount.

    Fields are a superset across all kinds; `_validate_kind_fields`
    enforces which fields each kind requires. Validation never reaches
    the database.
    """

    model_config = ConfigDict(frozen=True)

    kind: DiscountRuleKind
    value_cents: int | None = Field(default=None, ge=0)
    value_percent: int | None = Field(default=None, ge=0, le=100)
    min_subtotal_cents: int | None = Field(default=None, ge=0)
    max_discount_cents: int | None = Field(default=None, ge=0)
    buy_quantity: int | None = Field(default=None, ge=1)
    get_quantity: int | None = Field(default=None, ge=1)
    get_discount_percent: int | None = Field(default=None, ge=0, le=100)
    tiers: list[DiscountTier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> Self:
        """Each kind requires a specific subset of fields."""
        match self.kind:
            case DiscountRuleKind.PERCENTAGE:
                if self.value_percent is None:
                    raise ValueError("percentage discount requires value_percent")
            case DiscountRuleKind.FIXED:
                if self.value_cents is None:
                    raise ValueError("fixed discount requires value_cents")
            case DiscountRuleKind.BOGO:
                if self.buy_quantity is None or self.get_quantity is None:
                    raise ValueError(
                        "bogo discount requires buy_quantity and get_quantity"
                    )
                if self.get_discount_percent is None:
                    # default to free
                    object.__setattr__(self, "get_discount_percent", 100)
            case DiscountRuleKind.TIERED:
                if not self.tiers:
                    raise ValueError("tiered discount requires at least one tier")
            case DiscountRuleKind.FREE_SHIPPING:
                pass
        return self

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def calculate(self, context: DiscountContext) -> DiscountResult:
        """Compute the discount for the given context.

        Never returns a negative `discount_cents`; capping below zero is
        handled here so callers can sum results without worrying about
        signs. The `max_discount_cents` cap is also applied.
        """
        if self._below_minimum(context):
            return DiscountResult(
                discount_cents=0,
                explanation=(
                    f"subtotal below minimum ({self.min_subtotal_cents} cents)"
                ),
            )

        match self.kind:
            case DiscountRuleKind.FREE_SHIPPING:
                return DiscountResult(
                    discount_cents=0,
                    free_shipping=True,
                    explanation="free shipping",
                )
            case DiscountRuleKind.PERCENTAGE:
                return self._percentage(context)
            case DiscountRuleKind.FIXED:
                return self._fixed(context)
            case DiscountRuleKind.BOGO:
                return self._bogo(context)
            case DiscountRuleKind.TIERED:
                return self._tiered(context)

        return DiscountResult(discount_cents=0, explanation="unknown rule kind")

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _below_minimum(self, context: DiscountContext) -> bool:
        if self.min_subtotal_cents is None:
            return False
        return context.subtotal_cents < self.min_subtotal_cents

    def _cap(self, raw: int, context: DiscountContext) -> int:
        """Apply max_discount_cents and floor at the subtotal."""
        capped = max(0, raw)
        if self.max_discount_cents is not None:
            capped = min(capped, self.max_discount_cents)
        return min(capped, context.subtotal_cents)

    def _percentage(self, context: DiscountContext) -> DiscountResult:
        assert self.value_percent is not None  # validated
        raw = (context.subtotal_cents * self.value_percent) // 100
        capped = self._cap(raw, context)
        explanation = f"{self.value_percent}% off"
        if self.max_discount_cents is not None and capped == self.max_discount_cents:
            explanation += f" (capped at {self.max_discount_cents} cents)"
        return DiscountResult(discount_cents=capped, explanation=explanation)

    def _fixed(self, context: DiscountContext) -> DiscountResult:
        assert self.value_cents is not None  # validated
        capped = self._cap(self.value_cents, context)
        return DiscountResult(
            discount_cents=capped,
            explanation=f"{self.value_cents} cents off",
        )

    def _bogo(self, context: DiscountContext) -> DiscountResult:
        assert self.buy_quantity is not None and self.get_quantity is not None
        assert self.get_discount_percent is not None
        # Walk lines from cheapest unit-price upward — discount is applied
        # to the cheapest qualifying units (standard BOGO behavior).
        sorted_lines = sorted(context.line_items, key=lambda li: li.unit_price_cents)
        bundle_size = self.buy_quantity + self.get_quantity
        total_units = sum(li.quantity for li in sorted_lines)
        bundles = total_units // bundle_size
        if bundles == 0:
            return DiscountResult(discount_cents=0, explanation="bogo not met")

        # Number of units that get the discount.
        discounted_units = bundles * self.get_quantity
        discount_total = 0
        affected: list[UUID] = []
        remaining = discounted_units
        for line in sorted_lines:
            if remaining <= 0:
                break
            take = min(line.quantity, remaining)
            unit_off = (line.unit_price_cents * self.get_discount_percent) // 100
            discount_total += unit_off * take
            affected.append(line.product_id)
            remaining -= take

        capped = self._cap(discount_total, context)
        return DiscountResult(
            discount_cents=capped,
            affected_line_item_ids=affected,
            explanation=(
                f"buy {self.buy_quantity} get {self.get_quantity} "
                f"@ {self.get_discount_percent}% off — {bundles} bundle(s)"
            ),
        )

    def _tiered(self, context: DiscountContext) -> DiscountResult:
        # Highest threshold the cart meets wins.
        eligible = [
            t for t in self.tiers if context.subtotal_cents >= t.threshold_cents
        ]
        if not eligible:
            return DiscountResult(
                discount_cents=0,
                explanation="no tier threshold met",
            )
        winning = max(eligible, key=lambda t: t.threshold_cents)
        raw = (context.subtotal_cents * winning.percent) // 100
        capped = self._cap(raw, context)
        return DiscountResult(
            discount_cents=capped,
            explanation=(
                f"{winning.percent}% off (tier ≥ {winning.threshold_cents} cents)"
            ),
        )

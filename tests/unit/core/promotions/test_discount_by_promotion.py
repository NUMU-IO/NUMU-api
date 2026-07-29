"""`DiscountTotalResult.discount_by_promotion` — the per-promotion split.

Before this field existed the whole automatic total was attributed to
whichever promotion happened to be evaluated first, so a stacked cart
recorded "Trio offer −EGP 165 / Welcome 10 −EGP 0". These tests pin the
two properties the cart preview and the order snapshot depend on:

1. every entry equals that promotion's own contribution, and
2. the automatic entries sum EXACTLY to ``automatic_discount_cents`` —
   including after the subtotal-overflow trim, which unwinds from the
   last-applied automatic promotion backwards.
"""

from uuid import uuid4

from src.core.entities.promotion import Promotion
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.services.discount_calculator import DiscountCalculator
from src.core.value_objects.discount_rule import (
    CartLine,
    DiscountContext,
    DiscountResult,
    DiscountRule,
    DiscountRuleKind,
    LineFilter,
)
from src.core.value_objects.promotion_content import (
    AutomaticContent,
    DiscountCodeContent,
)


def _line(unit_cents: int, qty: int = 1) -> CartLine:
    return CartLine(product_id=uuid4(), quantity=qty, unit_price_cents=unit_cents)


def _ctx(lines: list[CartLine]) -> DiscountContext:
    return DiscountContext(
        subtotal_cents=sum(li.unit_price_cents * li.quantity for li in lines),
        line_items=lines,
    )


def _promo(surface: PromotionSurface, rule: DiscountRule) -> Promotion:
    return Promotion(
        tenant_id=uuid4(),
        store_id=uuid4(),
        name=f"{surface.value} promo",
        surface=surface,
        status=PromotionStatus.ACTIVE,
        coupon_id=uuid4() if surface == PromotionSurface.DISCOUNT_CODE else None,
        discount_rule=rule,
        content=DiscountCodeContent()
        if surface == PromotionSurface.DISCOUNT_CODE
        else AutomaticContent(),
    )


class _UncappedRule(DiscountRule):
    """Test double: returns a raw amount, ignoring the shared subtotal cap.

    Every shipped rule kind runs its result through ``_cap``, which floors
    at the (remaining) subtotal that ``calculate_total`` hands it — so the
    calculator's overflow trim is defensive code no production kind can
    currently reach. This double drives that branch directly rather than
    leaving the reconciliation contract untested.
    """

    def calculate(
        self,
        context: DiscountContext,
        *,
        buy_filter: LineFilter | None = None,
        get_filter: LineFilter | None = None,
    ) -> DiscountResult:
        return DiscountResult(
            discount_cents=self.value_cents or 0, explanation="uncapped test rule"
        )


# --------------------------------------------------------------------------- #
# F1 — two stacking automatics                                                #
# --------------------------------------------------------------------------- #


def test_each_automatic_records_its_own_contribution():
    """Trio offer + 10% off: 10000 and 6500, not 16500 and 0."""
    trio = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_quantity=3,
            multibuy_price_cents=65_000,
        ),
    )
    pct = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10),
    )
    ctx = _ctx([_line(25_000) for _ in range(3)])  # subtotal 75000

    res = DiscountCalculator().calculate_total([trio, pct], [], ctx)

    assert res.discount_by_promotion[trio.id] == 10_000
    # 10% of the REMAINING 65000 after the trio saving.
    assert res.discount_by_promotion[pct.id] == 6_500
    assert res.automatic_discount_cents == 16_500
    assert sum(res.discount_by_promotion.values()) == res.automatic_discount_cents


def test_code_and_automatic_are_both_recorded_and_automatics_reconcile():
    """The code entry is present too; the AUTOMATIC entries alone sum to
    `automatic_discount_cents` (the code has its own bucket)."""
    code = _promo(
        PromotionSurface.DISCOUNT_CODE,
        DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=20),
    )
    trio = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_quantity=3,
            multibuy_price_cents=65_000,
        ),
    )
    ctx = _ctx([_line(25_000) for _ in range(3)])

    res = DiscountCalculator().calculate_total([code, trio], [], ctx)

    assert res.discount_by_promotion[code.id] == 15_000
    assert res.discount_by_promotion[trio.id] == 10_000
    assert res.discount_by_promotion[trio.id] == res.automatic_discount_cents


def test_promotions_that_saved_nothing_are_absent_from_the_split():
    trio = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_quantity=3,
            multibuy_price_cents=65_000,
        ),
    )
    ship = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.FREE_SHIPPING),
    )
    ctx = _ctx([_line(25_000) for _ in range(3)])

    res = DiscountCalculator().calculate_total([trio, ship], [], ctx)

    assert res.free_shipping is True
    assert ship.id in res.applied_promotion_ids
    assert ship.id not in res.discount_by_promotion
    assert sum(res.discount_by_promotion.values()) == res.automatic_discount_cents


# --------------------------------------------------------------------------- #
# F2 — the subtotal-overflow trim                                             #
# --------------------------------------------------------------------------- #


def test_overflow_trim_keeps_the_split_reconciled():
    """Combined automatics exceed the subtotal → trim, then reconcile.

    Raw 800 + 800 against a 1000 subtotal. The bucket is trimmed to 1000;
    the split must still sum to 1000, taken off the LAST-applied promo
    first so the earliest (highest-priority) keeps its full amount.
    """
    first = _promo(
        PromotionSurface.AUTOMATIC,
        _UncappedRule(kind=DiscountRuleKind.FIXED, value_cents=800),
    )
    second = _promo(
        PromotionSurface.AUTOMATIC,
        _UncappedRule(kind=DiscountRuleKind.FIXED, value_cents=800),
    )
    ctx = _ctx([_line(1_000)])

    res = DiscountCalculator().calculate_total([first, second], [], ctx)

    assert res.automatic_discount_cents == 1_000
    assert sum(res.discount_by_promotion.values()) == res.automatic_discount_cents
    assert res.discount_by_promotion[first.id] == 800  # untouched
    assert res.discount_by_promotion[second.id] == 200  # absorbed the overflow


def test_overflow_trim_can_zero_the_last_promo_without_going_negative():
    """A big overflow unwinds whole promotions, never below zero."""
    first = _promo(
        PromotionSurface.AUTOMATIC,
        _UncappedRule(kind=DiscountRuleKind.FIXED, value_cents=1_000),
    )
    second = _promo(
        PromotionSurface.AUTOMATIC,
        _UncappedRule(kind=DiscountRuleKind.FIXED, value_cents=900),
    )
    ctx = _ctx([_line(1_000)])

    res = DiscountCalculator().calculate_total([first, second], [], ctx)

    assert res.automatic_discount_cents == 1_000
    assert res.discount_by_promotion[first.id] == 1_000
    assert res.discount_by_promotion[second.id] == 0
    assert sum(res.discount_by_promotion.values()) == res.automatic_discount_cents


def test_no_overflow_leaves_the_split_untouched():
    """Control: with the shipped kinds the trim never fires."""
    a = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=800),
    )
    b = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=800),
    )
    ctx = _ctx([_line(1_000)])

    res = DiscountCalculator().calculate_total([a, b], [], ctx)

    assert res.automatic_discount_cents == 1_000
    assert res.discount_by_promotion[a.id] == 800
    assert res.discount_by_promotion[b.id] == 200  # capped at the remainder
    assert sum(res.discount_by_promotion.values()) == 1_000

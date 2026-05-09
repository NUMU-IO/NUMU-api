"""Discount math: per-rule and stacked totals."""

from uuid import uuid4

import pytest

from src.core.entities.promotion import Promotion
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.services.discount_calculator import DiscountCalculator
from src.core.value_objects.discount_rule import (
    CartLine,
    DiscountContext,
    DiscountRule,
    DiscountRuleKind,
    DiscountTier,
)
from src.core.value_objects.promotion_content import (
    AutomaticContent,
    DiscountCodeContent,
)


def _line(unit_cents: int, qty: int = 1) -> CartLine:
    return CartLine(product_id=uuid4(), quantity=qty, unit_price_cents=unit_cents)


# -------- Per-rule math ------------------------------------------------------


def test_percentage_simple():
    rule = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=20)
    ctx = DiscountContext(subtotal_cents=10_000, line_items=[_line(10_000)])
    assert rule.calculate(ctx).discount_cents == 2_000


def test_percentage_capped_by_max():
    rule = DiscountRule(
        kind=DiscountRuleKind.PERCENTAGE,
        value_percent=20,
        max_discount_cents=1_000,
    )
    ctx = DiscountContext(subtotal_cents=10_000, line_items=[_line(10_000)])
    assert rule.calculate(ctx).discount_cents == 1_000


def test_fixed_cannot_exceed_subtotal():
    rule = DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=10_000)
    ctx = DiscountContext(subtotal_cents=5_000, line_items=[_line(5_000)])
    assert rule.calculate(ctx).discount_cents == 5_000


def test_below_minimum_yields_zero():
    rule = DiscountRule(
        kind=DiscountRuleKind.PERCENTAGE,
        value_percent=20,
        min_subtotal_cents=10_000,
    )
    ctx = DiscountContext(subtotal_cents=5_000, line_items=[_line(5_000)])
    assert rule.calculate(ctx).discount_cents == 0


def test_bogo_buy_2_get_1_free_on_cheapest():
    # Cart: 3x80 + 1x100 → 1 bundle of 3 (buy 2 get 1 free)
    # Discount = 1 unit @ 80 cents (cheapest unit)
    rule = DiscountRule(
        kind=DiscountRuleKind.BOGO,
        buy_quantity=2,
        get_quantity=1,
        get_discount_percent=100,
    )
    ctx = DiscountContext(
        subtotal_cents=80 * 3 + 100,
        line_items=[_line(80, qty=3), _line(100, qty=1)],
    )
    assert rule.calculate(ctx).discount_cents == 80


def test_bogo_set_filtered_applies_only_to_get_set():
    # Phase B: customer must buy from set A (the "buy_set"), but the
    # discount only goes to set B (the "get_set"). The buy-set lines
    # never get touched even though they're cheaper.
    buy_pid = uuid4()
    get_pid = uuid4()
    rule = DiscountRule(
        kind=DiscountRuleKind.BOGO,
        buy_quantity=2,
        get_quantity=1,
        get_discount_percent=100,
    )
    ctx = DiscountContext(
        subtotal_cents=2 * 50 + 200,
        line_items=[
            CartLine(product_id=buy_pid, quantity=2, unit_price_cents=50),
            CartLine(product_id=get_pid, quantity=1, unit_price_cents=200),
        ],
    )
    buy_filter = lambda li: li.product_id == buy_pid  # noqa: E731
    get_filter = lambda li: li.product_id == get_pid  # noqa: E731
    # 1 bundle: 2 from buy_set + 1 free from get_set → 200 cents off,
    # NOT 50 cents (buy_set is cheaper but excluded from the get-side).
    out = rule.calculate(ctx, buy_filter=buy_filter, get_filter=get_filter)
    assert out.discount_cents == 200


def test_bogo_set_filtered_skips_when_buy_set_qty_short():
    # Only 1 in the buy_set — can't form a bundle that needs 2.
    buy_pid = uuid4()
    get_pid = uuid4()
    rule = DiscountRule(
        kind=DiscountRuleKind.BOGO,
        buy_quantity=2,
        get_quantity=1,
        get_discount_percent=100,
    )
    ctx = DiscountContext(
        subtotal_cents=50 + 200,
        line_items=[
            CartLine(product_id=buy_pid, quantity=1, unit_price_cents=50),
            CartLine(product_id=get_pid, quantity=1, unit_price_cents=200),
        ],
    )
    buy_filter = lambda li: li.product_id == buy_pid  # noqa: E731
    get_filter = lambda li: li.product_id == get_pid  # noqa: E731
    out = rule.calculate(ctx, buy_filter=buy_filter, get_filter=get_filter)
    assert out.discount_cents == 0


def test_tiered_picks_highest_threshold_met():
    rule = DiscountRule(
        kind=DiscountRuleKind.TIERED,
        tiers=[
            DiscountTier(threshold_cents=50_000, percent=5),
            DiscountTier(threshold_cents=100_000, percent=10),
            DiscountTier(threshold_cents=200_000, percent=15),
        ],
    )
    # 120k → 10% tier
    ctx = DiscountContext(subtotal_cents=120_000, line_items=[_line(120_000)])
    assert rule.calculate(ctx).discount_cents == 12_000


def test_free_shipping_returns_flag_only():
    rule = DiscountRule(kind=DiscountRuleKind.FREE_SHIPPING)
    ctx = DiscountContext(subtotal_cents=10_000, line_items=[_line(10_000)])
    r = rule.calculate(ctx)
    assert r.discount_cents == 0
    assert r.free_shipping is True


# -------- Stacked totals (calculator) ----------------------------------------


def _promo(surface: PromotionSurface, rule: DiscountRule) -> Promotion:
    """Helper — minimal active promotion."""
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


def test_two_automatics_stack_additively():
    a = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10),
    )
    b = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=500),
    )
    ctx = DiscountContext(subtotal_cents=10_000, line_items=[_line(10_000)])
    res = DiscountCalculator().calculate_total([a, b], [], ctx)
    # 10% off 10000 = 1000, then fixed 500 off remaining = 500
    assert res.code_discount_cents == 0
    assert res.automatic_discount_cents == 1_500
    assert set(res.applied_promotion_ids) == {a.id, b.id}


def test_only_one_code_promo_wins():
    cheap = _promo(
        PromotionSurface.DISCOUNT_CODE,
        DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=500),
    )
    big = _promo(
        PromotionSurface.DISCOUNT_CODE,
        DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=20),
    )
    ctx = DiscountContext(subtotal_cents=10_000, line_items=[_line(10_000)])
    res = DiscountCalculator().calculate_total([cheap, big], [], ctx)
    assert res.code_discount_cents == 2_000  # the 20% wins
    assert res.applied_promotion_ids == [big.id]
    assert (cheap.id, "another code-based promo had higher savings") in res.rejected


def test_free_shipping_combines_with_percentage():
    pct = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=20),
    )
    ship = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.FREE_SHIPPING),
    )
    ctx = DiscountContext(subtotal_cents=10_000, line_items=[_line(10_000)])
    res = DiscountCalculator().calculate_total([pct, ship], [], ctx)
    assert res.automatic_discount_cents == 2_000
    assert res.free_shipping is True


def test_cap_at_subtotal_never_negative():
    huge = _promo(
        PromotionSurface.AUTOMATIC,
        DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=999_999),
    )
    ctx = DiscountContext(subtotal_cents=1_000, line_items=[_line(1_000)])
    res = DiscountCalculator().calculate_total([huge], [], ctx)
    assert res.total_discount_cents == 1_000


def test_calculate_one_passthrough():
    rule = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=15)
    ctx = DiscountContext(subtotal_cents=2_000, line_items=[_line(2_000)])
    r = DiscountCalculator().calculate_one(rule, ctx)
    assert r.discount_cents == 300


@pytest.mark.parametrize(
    "subtotal, percent, expected",
    [
        (0, 20, 0),
        (1, 100, 1),
        (99, 50, 49),  # integer division
        (10_000, 0, 0),
    ],
)
def test_percentage_edge_cases(subtotal, percent, expected):
    rule = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=percent)
    ctx = DiscountContext(subtotal_cents=subtotal, line_items=[_line(subtotal or 1)])
    assert rule.calculate(ctx).discount_cents == expected

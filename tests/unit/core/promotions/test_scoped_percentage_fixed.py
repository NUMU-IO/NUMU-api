"""Percentage / fixed discounts scoped to a product or category set.

"20% off Bags" was impossible before this: PERCENTAGE and FIXED ignored line
filters, so the only way to name a catalogue in the merchant UI was an
untagged target — which is an eligibility GATE. That gave 20% off the WHOLE
cart whenever a bag was in it, silently, with nothing in the UI to reveal it.

A `role="buy_set"` target is now the set the discount applies TO. These tests
pin both sides of that: the scoped arithmetic, and the unscoped behaviour that
every offer written before scoping existed still depends on.
"""

from uuid import UUID, uuid4

import pytest

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import (
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.services.discount_calculator import DiscountCalculator
from src.core.value_objects.discount_rule import (
    CartLine,
    DiscountContext,
    DiscountRule,
    DiscountRuleKind,
)
from src.core.value_objects.promotion_content import AutomaticContent

BAGS = uuid4()
SHOES = uuid4()


def _line(product_id: UUID, unit_cents: int, qty: int = 1, category=None) -> CartLine:
    return CartLine(
        product_id=product_id,
        quantity=qty,
        unit_price_cents=unit_cents,
        category_id=category,
    )


def _in_products(*ids: UUID):
    return lambda line: line.product_id in set(ids)


# -------- Percentage ---------------------------------------------------------


def test_percentage_scopes_to_matching_lines():
    bag, shoe = uuid4(), uuid4()
    rule = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=20)
    ctx = DiscountContext(
        subtotal_cents=30_000,
        line_items=[_line(bag, 10_000), _line(shoe, 20_000)],
    )
    # 20% of the bag alone, not of the 300.00 cart.
    result = rule.calculate(ctx, buy_filter=_in_products(bag))
    assert result.discount_cents == 2_000


def test_percentage_counts_quantity_within_the_scope():
    bag, shoe = uuid4(), uuid4()
    rule = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=50)
    ctx = DiscountContext(
        subtotal_cents=50_000,
        line_items=[_line(bag, 10_000, qty=3), _line(shoe, 20_000)],
    )
    assert rule.calculate(ctx, buy_filter=_in_products(bag)).discount_cents == 15_000


def test_percentage_without_a_filter_is_unchanged():
    rule = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=20)
    ctx = DiscountContext(subtotal_cents=30_000, line_items=[_line(uuid4(), 30_000)])
    assert rule.calculate(ctx).discount_cents == 6_000


def test_percentage_scope_matching_nothing_discounts_nothing():
    rule = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=20)
    ctx = DiscountContext(subtotal_cents=20_000, line_items=[_line(uuid4(), 20_000)])
    assert rule.calculate(ctx, buy_filter=_in_products(BAGS)).discount_cents == 0


def test_percentage_scope_still_honours_the_max_cap():
    bag = uuid4()
    rule = DiscountRule(
        kind=DiscountRuleKind.PERCENTAGE,
        value_percent=50,
        max_discount_cents=1_000,
    )
    ctx = DiscountContext(subtotal_cents=20_000, line_items=[_line(bag, 20_000)])
    assert rule.calculate(ctx, buy_filter=_in_products(bag)).discount_cents == 1_000


def test_minimum_gates_on_the_whole_cart_not_the_scope():
    # "Spend 200, get 20% off bags" — the 200 is a condition on the cart, so a
    # 300.00 cart qualifies even though its bags are only worth 100.00.
    bag, shoe = uuid4(), uuid4()
    rule = DiscountRule(
        kind=DiscountRuleKind.PERCENTAGE,
        value_percent=20,
        min_subtotal_cents=20_000,
    )
    ctx = DiscountContext(
        subtotal_cents=30_000,
        line_items=[_line(bag, 10_000), _line(shoe, 20_000)],
    )
    assert rule.calculate(ctx, buy_filter=_in_products(bag)).discount_cents == 2_000


def test_percentage_falls_back_to_subtotal_without_line_items():
    # The legacy /coupons/apply preview knows an order total and nothing else.
    # Reporting the unscoped figure beats reporting zero; order-create always
    # has the lines and recomputes authoritatively.
    rule = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10)
    ctx = DiscountContext(subtotal_cents=10_000, line_items=[])
    assert rule.calculate(ctx, buy_filter=_in_products(BAGS)).discount_cents == 1_000


# -------- Fixed --------------------------------------------------------------


def test_fixed_scope_is_capped_by_what_the_matching_lines_are_worth():
    # "EGP 50 off bags" against a EGP 30 bag is EGP 30 — the rest of the cart
    # does not fund the discount.
    bag, shoe = uuid4(), uuid4()
    rule = DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=5_000)
    ctx = DiscountContext(
        subtotal_cents=23_000,
        line_items=[_line(bag, 3_000), _line(shoe, 20_000)],
    )
    assert rule.calculate(ctx, buy_filter=_in_products(bag)).discount_cents == 3_000


def test_fixed_scope_under_the_line_value_is_the_full_amount():
    bag = uuid4()
    rule = DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=5_000)
    ctx = DiscountContext(subtotal_cents=30_000, line_items=[_line(bag, 30_000)])
    assert rule.calculate(ctx, buy_filter=_in_products(bag)).discount_cents == 5_000


def test_fixed_without_a_filter_is_unchanged():
    rule = DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=5_000)
    ctx = DiscountContext(subtotal_cents=30_000, line_items=[_line(uuid4(), 30_000)])
    assert rule.calculate(ctx).discount_cents == 5_000


# -------- Through the calculator, from real targets --------------------------


def _promo(rule: DiscountRule) -> Promotion:
    return Promotion(
        tenant_id=uuid4(),
        store_id=uuid4(),
        name="Category sale",
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        discount_rule=rule,
        content=AutomaticContent(),
    )


def _target(promo: Promotion, kind: TargetKind, value: dict) -> PromotionTarget:
    return PromotionTarget(
        tenant_id=promo.tenant_id,
        promotion_id=promo.id,
        target_kind=kind,
        target_value=value,
        inclusion=True,
        role="buy_set",
    )


@pytest.mark.parametrize("percent,expected", [(20, 2_000), (100, 10_000)])
def test_calculator_scopes_a_percentage_to_a_category(percent, expected):
    bag_line = _line(uuid4(), 10_000, category=BAGS)
    shoe_line = _line(uuid4(), 20_000, category=SHOES)
    promo = _promo(
        DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=percent)
    )
    targets = [_target(promo, TargetKind.CATEGORY, {"category_ids": [str(BAGS)]})]

    out = DiscountCalculator().calculate_total(
        [promo],
        [],
        DiscountContext(subtotal_cents=30_000, line_items=[bag_line, shoe_line]),
        targets_by_promotion={promo.id: targets},
    )
    assert out.automatic_discount_cents == expected


def test_calculator_scopes_a_percentage_to_named_products():
    bag = uuid4()
    promo = _promo(DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=25))
    targets = [_target(promo, TargetKind.PRODUCT, {"product_ids": [str(bag)]})]

    out = DiscountCalculator().calculate_total(
        [promo],
        [],
        DiscountContext(
            subtotal_cents=30_000,
            line_items=[_line(bag, 10_000), _line(uuid4(), 20_000)],
        ),
        targets_by_promotion={promo.id: targets},
    )
    assert out.automatic_discount_cents == 2_500


def test_untagged_catalog_target_still_gates_the_whole_cart():
    # The old meaning is deliberately preserved: a target with no role is an
    # eligibility condition, and the discount then applies cart-wide. Existing
    # promotions written that way must not silently change shape.
    bag = uuid4()
    promo = _promo(DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10))
    gate = PromotionTarget(
        tenant_id=promo.tenant_id,
        promotion_id=promo.id,
        target_kind=TargetKind.PRODUCT,
        target_value={"product_ids": [str(bag)]},
        inclusion=True,
    )
    out = DiscountCalculator().calculate_total(
        [promo],
        [],
        DiscountContext(
            subtotal_cents=30_000,
            line_items=[_line(bag, 10_000), _line(uuid4(), 20_000)],
        ),
        targets_by_promotion={promo.id: [gate]},
    )
    assert out.automatic_discount_cents == 3_000

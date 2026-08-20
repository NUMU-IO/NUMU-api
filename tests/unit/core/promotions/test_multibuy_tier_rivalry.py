"""Two multibuy tiers written as two promotions must not both fire.

`DiscountRule.multibuy_tiers` is the real fix — one rule cannot double-count
against itself. This suite covers the net under the promotions ALREADY LIVE,
which no migration can safely rewrite, because two rows that look like a ladder
might in principle have been meant to stack.

The worked example is the reference catalogue's Cap Stack: caps at EGP 550,
"2 for 968" and "3 for 1,320" as two separate automatic promotions over the
Caps collection.
"""

from uuid import UUID, uuid4

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

CAP = 55_000
TEE = 69_000
CAPS_2 = 96_800
CAPS_3 = 132_000

CAPS = uuid4()
TEES = uuid4()


def _line(
    unit_cents: int, qty: int = 1, *, category_id: UUID | None = None
) -> CartLine:
    return CartLine(
        product_id=uuid4(),
        quantity=qty,
        unit_price_cents=unit_cents,
        category_id=category_id,
    )


def _ctx(lines: list[CartLine]) -> DiscountContext:
    return DiscountContext(
        subtotal_cents=sum(li.unit_price_cents * li.quantity for li in lines),
        line_items=lines,
    )


def _promo(rule: DiscountRule) -> Promotion:
    return Promotion(
        id=uuid4(),
        tenant_id=uuid4(),
        store_id=uuid4(),
        name="offer",
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        content=AutomaticContent(),
        discount_rule=rule,
    )


def _tier_promo(quantity: int, price_cents: int) -> Promotion:
    return _promo(
        DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_quantity=quantity,
            multibuy_price_cents=price_cents,
        )
    )


def _buy_set(promo: Promotion, category_id: UUID) -> PromotionTarget:
    return PromotionTarget(
        id=uuid4(),
        tenant_id=promo.tenant_id,
        promotion_id=promo.id,
        target_kind=TargetKind.CATEGORY,
        target_value={"category_ids": [str(category_id)]},
        inclusion=True,
        role="buy_set",
    )


def _rival_pair() -> tuple[list[Promotion], dict[UUID, list[PromotionTarget]]]:
    """Cap Stack as it exists in production today — one promotion per tier."""
    two = _tier_promo(2, CAPS_2)
    three = _tier_promo(3, CAPS_3)
    return [two, three], {
        two.id: [_buy_set(two, CAPS)],
        three.id: [_buy_set(three, CAPS)],
    }


# --------------------------------------------------------------------------- #


def test_two_tier_promotions_no_longer_double_discount():
    """The money bug, pinned.

    Before the rivalry pass the 2-for took the top two units for 13,200 AND
    the 3-for took all three for 33,000 — 46,200 off a 165,000 cart, charging
    118,800 against an advertised 132,000, on every three-cap order.
    """
    promos, targets = _rival_pair()
    ctx = _ctx([_line(CAP, 3, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        promos, [], ctx, targets_by_promotion=targets
    )

    assert result.automatic_discount_cents == 33_000
    assert ctx.subtotal_cents - result.automatic_discount_cents == CAPS_3


def test_the_losing_tier_is_reported_with_the_fix():
    """A merchant reading the debug output learns their ladder should be one row."""
    promos, targets = _rival_pair()
    ctx = _ctx([_line(CAP, 3, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        promos, [], ctx, targets_by_promotion=targets
    )

    assert len(result.applied_promotion_ids) == 1
    assert any("multibuy_tiers" in reason for _, reason in result.rejected)


def test_the_winner_is_chosen_per_cart_not_per_configuration():
    """Two caps: the 3-for cannot fire, so the 2-for must still apply."""
    promos, targets = _rival_pair()
    ctx = _ctx([_line(CAP, 2, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        promos, [], ctx, targets_by_promotion=targets
    )

    assert ctx.subtotal_cents - result.automatic_discount_cents == CAPS_2


def test_multibuys_over_different_scopes_both_survive():
    """Rivalry is per catalogue — "2 caps" and "2 tees" are separate offers."""
    caps = _tier_promo(2, CAPS_2)
    tees = _tier_promo(2, 124_000)
    targets = {
        caps.id: [_buy_set(caps, CAPS)],
        tees.id: [_buy_set(tees, TEES)],
    }
    ctx = _ctx([
        _line(CAP, 2, category_id=CAPS),
        _line(TEE, 2, category_id=TEES),
    ])

    result = DiscountCalculator().calculate_total(
        [caps, tees], [], ctx, targets_by_promotion=targets
    )

    # caps 110,000 → 96,800 saves 13,200; tees 138,000 → 124,000 saves 14,000.
    assert result.automatic_discount_cents == 27_200
    assert set(result.applied_promotion_ids) == {caps.id, tees.id}


def test_a_lone_multibuy_is_untouched():
    promo = _tier_promo(3, CAPS_3)
    targets = {promo.id: [_buy_set(promo, CAPS)]}
    ctx = _ctx([_line(CAP, 3, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    assert result.automatic_discount_cents == 33_000
    assert result.rejected == []


def test_a_multibuy_does_not_suppress_other_rule_kinds():
    """Only multibuys rival each other; a percentage promo still stacks."""
    caps = _tier_promo(2, CAPS_2)
    ten_pct = _promo(DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10))
    targets = {caps.id: [_buy_set(caps, CAPS)]}
    ctx = _ctx([_line(CAP, 2, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        [caps, ten_pct], [], ctx, targets_by_promotion=targets
    )

    assert set(result.applied_promotion_ids) == {caps.id, ten_pct.id}


def test_unscoped_multibuys_rival_each_other():
    """Two store-wide ladders are still one ladder."""
    three = _tier_promo(3, 65_000)
    five = _tier_promo(5, 100_000)
    ctx = _ctx([_line(25_000, 5)])

    result = DiscountCalculator().calculate_total(
        [three, five], [], ctx, targets_by_promotion={three.id: [], five.id: []}
    )

    # 5 units at 25,000 = 125,000. The 5-for saves 25,000; the 3-for saves
    # 10,000. Stacked they took 35,000 and charged 90,000 for a "100,000" offer.
    assert result.automatic_discount_cents == 25_000
    assert result.applied_promotion_ids == [five.id]

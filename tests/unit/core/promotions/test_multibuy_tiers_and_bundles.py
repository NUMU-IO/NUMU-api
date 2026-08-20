"""Multi-tier MULTIBUY and the BUNDLE rule kind.

Both exist to make one page honest: a Build-a-Bundle chooser that shows four
offers and charges what it showed. The worked numbers throughout are the
reference catalogue that page was built against
(`V3-themes/teen-engine-V3/BUNDLES.md`):

    tee    EGP 690    cap  EGP 550    towel EGP 590

    Cap Stack     2 caps for 968, 3 caps for 1,320
    The Uniform   1 tee + 1 cap for 1,091

Coverage order follows the existing multibuy suite: happy path → the
regression each feature was written for → boundaries/guards → validation →
scoping.
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_target import (
    PromotionTarget,
    leg_index,
    leg_role,
)
from src.core.enums.promotion_enums import (
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.services.discount_calculator import DiscountCalculator
from src.core.value_objects.discount_rule import (
    MAX_BUNDLE_LEGS,
    BundleLeg,
    CartLine,
    DiscountContext,
    DiscountRule,
    DiscountRuleKind,
    MultibuyTier,
)
from src.core.value_objects.promotion_content import AutomaticContent

# The reference catalogue, in cents.
TEE = 69_000
CAP = 55_000
TOWEL = 59_000

CAPS_2 = 96_800
CAPS_3 = 132_000
UNIFORM = 109_100

TEES = uuid4()
CAPS = uuid4()
TOWELS = uuid4()


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


def _target(promo: Promotion, role: str, *, category_id: UUID) -> PromotionTarget:
    return PromotionTarget(
        id=uuid4(),
        tenant_id=promo.tenant_id,
        promotion_id=promo.id,
        target_kind=TargetKind.CATEGORY,
        target_value={"category_ids": [str(category_id)]},
        inclusion=True,
        role=role,
    )


# --------------------------------------------------------------------------- #
# Multi-tier multibuy — the regression                                         #
# --------------------------------------------------------------------------- #

CAP_STACK = DiscountRule(
    kind=DiscountRuleKind.MULTIBUY,
    multibuy_tiers=[
        MultibuyTier(quantity=2, price_cents=CAPS_2),
        MultibuyTier(quantity=3, price_cents=CAPS_3),
    ],
)


@pytest.mark.parametrize(
    ("caps", "expected_charge"),
    [
        (1, CAP),  # no tier reached
        (2, CAPS_2),  # 2-for
        (3, CAPS_3),  # 3-for — the regression case
        (4, CAPS_3 + CAP),  # 3-for, one at list
        (5, CAPS_3 + CAPS_2),  # 3-for + 2-for
        (6, CAPS_3 * 2),  # two 3-fors
    ],
)
def test_cap_stack_charges_the_advertised_price(caps: int, expected_charge: int):
    """The whole point: what the chooser advertises is what the cart charges.

    Split across two promotions this was wrong at every count above two —
    automatic promotions stack additively and the two tiers did not share unit
    allocation, so three caps triggered both and charged 1,188 against an
    advertised 1,320.
    """
    ctx = _ctx([_line(CAP, caps)])
    result = CAP_STACK.calculate(ctx)
    assert ctx.subtotal_cents - result.discount_cents == expected_charge


def test_two_tiers_on_one_rule_cannot_double_count():
    """Three caps consume three units — not two plus three."""
    ctx = _ctx([_line(CAP, 3)])
    result = CAP_STACK.calculate(ctx)
    # 1650 regular - 1320 offer. The old two-promotion shape produced 462.
    assert result.discount_cents == 33_000


def test_tier_choice_maximises_saving_per_unit_not_total():
    """A generous small tier beats a bigger, stingier one.

    Selection by TOTAL saving would take the 3-for here (saving 15,000) and
    leave the shopper worse off than two applications of the 2-for
    (10,000 each). Per-unit is also the version that cannot be gamed by
    dropping one cheap extra item in the bag.
    """
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_tiers=[
            MultibuyTier(quantity=2, price_cents=40_000),  # saves 10,000 (5,000/unit)
            MultibuyTier(quantity=3, price_cents=60_000),  # saves 15,000 (5,000/unit)
        ],
    )
    # 4 units at 25,000. Per-unit ties at 5,000, so the first tier scanned wins
    # and the remainder is re-evaluated — either way nothing is double-counted.
    ctx = _ctx([_line(25_000, 4)])
    result = rule.calculate(ctx)
    assert result.discount_cents == 20_000
    assert ctx.subtotal_cents - result.discount_cents == 80_000


def test_generous_small_tier_is_preferred():
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_tiers=[
            MultibuyTier(quantity=2, price_cents=20_000),  # saves 30,000 (15,000/unit)
            MultibuyTier(quantity=3, price_cents=60_000),  # saves 15,000 (5,000/unit)
        ],
    )
    ctx = _ctx([_line(25_000, 2)])
    assert rule.calculate(ctx).discount_cents == 30_000


def test_tiers_stop_before_charging_more_than_regular_price():
    """A tier priced above the cart's own total never applies."""
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_tiers=[MultibuyTier(quantity=2, price_cents=CAPS_2)],
    )
    cheap = _ctx([_line(10_000, 2)])  # 20,000 regular vs a 96,800 "offer"
    result = rule.calculate(cheap)
    assert result.discount_cents == 0
    assert "not below the" in result.explanation


def test_partial_tier_stops_at_the_first_non_saving_group():
    """Units are descending, so one non-saving group proves the rest are too."""
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_tiers=[MultibuyTier(quantity=2, price_cents=50_000)],
    )
    ctx = _ctx([_line(40_000, 2), _line(10_000, 2)])
    # First group 80,000 > 50,000 → saves 30,000. Second group is 20,000 → skipped.
    assert rule.calculate(ctx).discount_cents == 30_000


# --------------------------------------------------------------------------- #
# Multi-tier multibuy — backward compatibility and validation                  #
# --------------------------------------------------------------------------- #


def test_legacy_scalar_rule_is_unchanged():
    """Every promotion written before tiers existed keeps its exact behaviour."""
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_quantity=3,
        multibuy_price_cents=65_000,
    )
    ctx = _ctx([_line(25_000, 3)])
    assert rule.calculate(ctx).discount_cents == 10_000


def test_scalar_pair_is_folded_in_as_a_tier():
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_quantity=2,
        multibuy_price_cents=CAPS_2,
        multibuy_tiers=[MultibuyTier(quantity=3, price_cents=CAPS_3)],
    )
    assert [(t.quantity, t.price_cents) for t in rule.resolved_multibuy_tiers] == [
        (3, CAPS_3),
        (2, CAPS_2),
    ]


def test_explicit_tier_wins_over_the_scalar_pair_for_the_same_quantity():
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_quantity=3,
        multibuy_price_cents=99_999,
        multibuy_tiers=[MultibuyTier(quantity=3, price_cents=CAPS_3)],
    )
    assert rule.resolved_multibuy_tiers == [
        MultibuyTier(quantity=3, price_cents=CAPS_3)
    ]


def test_tiers_alone_satisfy_validation():
    DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_tiers=[MultibuyTier(quantity=2, price_cents=CAPS_2)],
    )


def test_neither_shape_is_rejected():
    with pytest.raises(ValidationError):
        DiscountRule(kind=DiscountRuleKind.MULTIBUY)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"multibuy_quantity": 3},
        {"multibuy_price_cents": 65_000},
    ],
)
def test_half_a_scalar_pair_is_rejected_even_with_tiers(kwargs):
    """A stray half-pair is a typo, and a typo here prices at the wrong tier."""
    with pytest.raises(ValidationError):
        DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_tiers=[MultibuyTier(quantity=2, price_cents=CAPS_2)],
            **kwargs,
        )


def test_duplicate_tier_quantities_are_rejected():
    with pytest.raises(ValidationError):
        DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_tiers=[
                MultibuyTier(quantity=2, price_cents=CAPS_2),
                MultibuyTier(quantity=2, price_cents=90_000),
            ],
        )


def test_tier_quantity_of_one_is_rejected():
    with pytest.raises(ValidationError):
        MultibuyTier(quantity=1, price_cents=CAPS_2)


def test_tiered_rule_round_trips_through_json():
    restored = DiscountRule.model_validate_json(CAP_STACK.model_dump_json())
    assert restored == CAP_STACK


# --------------------------------------------------------------------------- #
# BUNDLE — the offer MULTIBUY cannot express                                   #
# --------------------------------------------------------------------------- #

UNIFORM_RULE = DiscountRule(
    kind=DiscountRuleKind.BUNDLE,
    bundle_legs=[
        BundleLeg(quantity=1, label="1 tee"),
        BundleLeg(quantity=1, label="1 cap"),
    ],
    bundle_price_cents=UNIFORM,
)


def _uniform_promo() -> tuple[Promotion, dict[UUID, list[PromotionTarget]]]:
    promo = _promo(UNIFORM_RULE)
    targets = [
        _target(promo, leg_role(0), category_id=TEES),
        _target(promo, leg_role(1), category_id=CAPS),
    ]
    return promo, {promo.id: targets}


def test_uniform_bundle_charges_the_advertised_price():
    promo, targets = _uniform_promo()
    ctx = _ctx([_line(TEE, category_id=TEES), _line(CAP, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    assert ctx.subtotal_cents == 124_000
    assert result.automatic_discount_cents == 14_900  # save LE 149
    assert ctx.subtotal_cents - result.automatic_discount_cents == UNIFORM


def test_two_tees_do_not_form_a_tee_plus_cap_bundle():
    """The reason this rule kind exists.

    A MULTIBUY scoped to {tees ∪ caps} with N=2 fires here and charges the
    bundle price for two tees. The legs make the second one unfillable.
    """
    promo, targets = _uniform_promo()
    ctx = _ctx([_line(TEE, 2, category_id=TEES)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    assert result.automatic_discount_cents == 0


def test_bundle_repeats_for_a_doubled_basket():
    promo, targets = _uniform_promo()
    ctx = _ctx([_line(TEE, 2, category_id=TEES), _line(CAP, 2, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    assert result.automatic_discount_cents == 14_900 * 2


def test_bundle_leftovers_stay_at_list_price():
    promo, targets = _uniform_promo()
    ctx = _ctx([_line(TEE, 2, category_id=TEES), _line(CAP, 1, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    # One bundle formed; the spare tee is untouched.
    assert result.automatic_discount_cents == 14_900
    assert ctx.subtotal_cents - result.automatic_discount_cents == UNIFORM + TEE


def test_three_leg_summer_bundle():
    rule = DiscountRule(
        kind=DiscountRuleKind.BUNDLE,
        bundle_legs=[BundleLeg(quantity=1) for _ in range(3)],
        bundle_price_cents=155_600,
    )
    promo = _promo(rule)
    targets = {
        promo.id: [
            _target(promo, leg_role(0), category_id=TEES),
            _target(promo, leg_role(1), category_id=CAPS),
            _target(promo, leg_role(2), category_id=TOWELS),
        ]
    }
    ctx = _ctx([
        _line(TEE, category_id=TEES),
        _line(CAP, category_id=CAPS),
        _line(TOWEL, category_id=TOWELS),
    ])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    assert ctx.subtotal_cents == 183_000  # LE 1,830
    assert result.automatic_discount_cents == 27_400  # save LE 274


def test_bundle_never_charges_more_than_regular_price():
    """A cheap basket is left alone rather than marked UP to the bundle price."""
    promo, targets = _uniform_promo()
    ctx = _ctx([_line(1_000, category_id=TEES), _line(1_000, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    assert result.automatic_discount_cents == 0


def test_bundle_takes_the_most_expensive_unit_per_leg():
    """Same customer-optimal rule as multibuy: never the shopper's cheapest."""
    promo, targets = _uniform_promo()
    ctx = _ctx([
        _line(TEE, category_id=TEES),
        _line(20_000, category_id=TEES),
        _line(CAP, category_id=CAPS),
    ])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    # The 690 tee is chosen, not the 200 one: 124,000 - 109,100.
    assert result.automatic_discount_cents == 14_900


def test_a_unit_is_spent_by_only_one_leg():
    """Overlapping legs must not both claim the same item."""
    rule = DiscountRule(
        kind=DiscountRuleKind.BUNDLE,
        bundle_legs=[BundleLeg(quantity=1), BundleLeg(quantity=1)],
        bundle_price_cents=100_000,
    )
    promo = _promo(rule)
    # BOTH legs scoped to tees — one tee in the cart cannot satisfy both.
    targets = {
        promo.id: [
            _target(promo, leg_role(0), category_id=TEES),
            _target(promo, leg_role(1), category_id=TEES),
        ]
    }
    ctx = _ctx([_line(TEE, category_id=TEES)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    assert result.automatic_discount_cents == 0


def test_a_stale_leg_target_is_ignored():
    """Shortening a bundle must not shift the surviving legs' scopes."""
    promo, targets = _uniform_promo()
    targets[promo.id].append(_target(promo, leg_role(7), category_id=TOWELS))
    ctx = _ctx([_line(TEE, category_id=TEES), _line(CAP, category_id=CAPS)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion=targets
    )

    assert result.automatic_discount_cents == 14_900


def test_bundle_without_targets_is_not_scoped():
    """No targets ⇒ every leg matches anything, same as an unscoped multibuy."""
    promo = _promo(UNIFORM_RULE)
    ctx = _ctx([_line(TEE), _line(CAP)])

    result = DiscountCalculator().calculate_total(
        [promo], [], ctx, targets_by_promotion={promo.id: []}
    )

    assert result.automatic_discount_cents == 14_900


# --------------------------------------------------------------------------- #
# BUNDLE — validation                                                          #
# --------------------------------------------------------------------------- #


def test_bundle_requires_a_price():
    with pytest.raises(ValidationError):
        DiscountRule(
            kind=DiscountRuleKind.BUNDLE,
            bundle_legs=[BundleLeg(), BundleLeg()],
        )


def test_bundle_requires_legs():
    with pytest.raises(ValidationError):
        DiscountRule(kind=DiscountRuleKind.BUNDLE, bundle_price_cents=UNIFORM)


def test_single_leg_bundle_is_rejected_as_a_multibuy():
    with pytest.raises(ValidationError):
        DiscountRule(
            kind=DiscountRuleKind.BUNDLE,
            bundle_legs=[BundleLeg(quantity=2)],
            bundle_price_cents=UNIFORM,
        )


def test_too_many_legs_is_rejected():
    with pytest.raises(ValidationError):
        DiscountRule(
            kind=DiscountRuleKind.BUNDLE,
            bundle_legs=[BundleLeg() for _ in range(MAX_BUNDLE_LEGS + 1)],
            bundle_price_cents=UNIFORM,
        )


def test_max_legs_is_the_boundary_and_is_accepted():
    DiscountRule(
        kind=DiscountRuleKind.BUNDLE,
        bundle_legs=[BundleLeg() for _ in range(MAX_BUNDLE_LEGS)],
        bundle_price_cents=UNIFORM,
    )


def test_bundle_rule_round_trips_through_json():
    restored = DiscountRule.model_validate_json(UNIFORM_RULE.model_dump_json())
    assert restored == UNIFORM_RULE


# --------------------------------------------------------------------------- #
# Leg roles                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("leg:0", 0),
        ("leg:7", 7),
        ("leg:12", 12),
        ("buy_set", None),
        ("get_set", None),
        (None, None),
        ("leg:", None),
        ("leg:O", None),  # letter O — the typo the pattern exists to catch
        ("legs:1", None),
    ],
)
def test_leg_index_parsing(role, expected):
    assert leg_index(role) == expected


def test_leg_role_round_trips():
    assert leg_index(leg_role(3)) == 3


@pytest.mark.parametrize("role", ["leg:O", "legs:0", "buyset", "leg:-1", "leg:100"])
def test_malformed_roles_are_rejected_at_the_boundary(role):
    """A role that scopes nothing would make a leg match the whole catalogue."""
    with pytest.raises(ValidationError):
        PromotionTarget(
            tenant_id=uuid4(),
            promotion_id=uuid4(),
            target_kind=TargetKind.CATEGORY,
            target_value={"category_ids": [str(TEES)]},
            role=role,
        )

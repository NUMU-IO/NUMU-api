"""MULTIBUY discount math — "any N eligible items for a fixed total P".

The specification these tests encode is
``docs/Plans/OFFER-VIONNE.md`` §4 (customer-experience matrix) and §5
(normative math spec). The worked example throughout is the vionne offer:
**3 items for EGP 650** → ``multibuy_quantity=3``,
``multibuy_price_cents=65000``, items at 25000 cents (EGP 250) unless a
test says otherwise.

Coverage order follows the QA methodology: happy path → validation →
boundaries/guards → caps → scoping → stacking → unknown-data tolerance.
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

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
from src.core.value_objects.promotion_content import (
    AutomaticContent,
    DiscountCodeContent,
)

# The vionne offer, in cents.
N = 3
P = 65_000
UNIT = 25_000


def _line(
    unit_cents: int,
    qty: int = 1,
    *,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
) -> CartLine:
    return CartLine(
        product_id=product_id or uuid4(),
        quantity=qty,
        unit_price_cents=unit_cents,
        category_id=category_id,
    )


def _ctx(lines: list[CartLine]) -> DiscountContext:
    subtotal = sum(li.unit_price_cents * li.quantity for li in lines)
    return DiscountContext(subtotal_cents=subtotal, line_items=lines)


def _multibuy_rule(**overrides) -> DiscountRule:
    kwargs: dict = {
        "kind": DiscountRuleKind.MULTIBUY,
        "multibuy_quantity": N,
        "multibuy_price_cents": P,
    }
    kwargs.update(overrides)
    return DiscountRule(**kwargs)


def _promo(surface: PromotionSurface, rule: DiscountRule) -> Promotion:
    """Minimal active promotion — mirrors test_discount_calculator.py."""
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


# --------------------------------------------------------------------------- #
# A. The §4 matrix — unit counts                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "units, expected",
    [
        (0, 0),
        (2, 0),
        (3, 10_000),
        (4, 10_000),
        (5, 10_000),
        (6, 20_000),
        (7, 20_000),
    ],
)
def test_multibuy_unit_matrix(units: int, expected: int):
    """§4: groups repeat every N units; remainders stay at full price."""
    rule = _multibuy_rule()
    ctx = _ctx([_line(UNIT) for _ in range(units)])
    assert rule.calculate(ctx).discount_cents == expected


def test_multibuy_below_threshold_explains_what_is_missing():
    """0–2 units: no discount, and the reason names the required count."""
    rule = _multibuy_rule()
    out = rule.calculate(_ctx([_line(UNIT), _line(UNIT)]))
    assert out.discount_cents == 0
    assert "3" in out.explanation
    assert out.affected_line_item_ids == []


def test_multibuy_counts_units_not_lines():
    """One line of quantity 3 IS a trio — Mix & Match allows repeats.

    A line-based implementation returns 0 here; this is the regression
    that case guards.
    """
    pid = uuid4()
    rule = _multibuy_rule()
    ctx = _ctx([_line(UNIT, qty=3, product_id=pid)])
    out = rule.calculate(ctx)
    assert out.discount_cents == 10_000
    # De-duplicated: three units of one product report the product once.
    assert out.affected_line_item_ids == [pid]


def test_multibuy_groups_most_expensive_units_first():
    """§4 mixed-prices row: [300, 300, 250, 200] → 850 − 650 = 200."""
    p30a, p30b, p25, p20 = uuid4(), uuid4(), uuid4(), uuid4()
    rule = _multibuy_rule()
    ctx = _ctx([
        _line(30_000, product_id=p30a),
        _line(30_000, product_id=p30b),
        _line(25_000, product_id=p25),
        _line(20_000, product_id=p20),
    ])
    out = rule.calculate(ctx)
    assert out.discount_cents == 20_000
    # Only the three most expensive units formed the group.
    assert set(out.affected_line_item_ids) == {p30a, p30b, p25}
    assert p20 not in out.affected_line_item_ids


# --------------------------------------------------------------------------- #
# A (cont). The two "never make the customer worse off" guards                #
# --------------------------------------------------------------------------- #


def test_multibuy_cheap_cart_guard_never_charges_more_than_regular():
    """§4: 3 × 200 = 600 ≤ 650 → offer silently does not apply."""
    rule = _multibuy_rule()
    ctx = _ctx([_line(20_000) for _ in range(3)])
    out = rule.calculate(ctx)
    assert out.discount_cents == 0
    assert "not below the regular price" in out.explanation
    assert out.affected_line_item_ids == []


def test_multibuy_group_exactly_at_the_offer_price_does_not_apply():
    """Boundary: §5 stops on `group_sum <= P`, so equality means no offer."""
    rule = _multibuy_rule()
    ctx = _ctx([_line(25_000), _line(25_000), _line(15_000)])  # exactly 65000
    out = rule.calculate(ctx)
    assert out.discount_cents == 0
    assert "not below the regular price" in out.explanation


def test_multibuy_one_cent_above_the_offer_price_applies():
    """The other side of the boundary — a single piaster of saving stands."""
    rule = _multibuy_rule()
    ctx = _ctx([_line(25_000), _line(25_000), _line(15_001)])  # 65001
    assert rule.calculate(ctx).discount_cents == 1


def test_multibuy_partial_guard_stops_at_the_first_non_saving_group():
    """A saving first group must NOT drag a losing second group along.

    Units [300, 300, 300, 200, 200, 200]:
      group 1 = 900 − 650 = 250 saved
      group 2 = 600 ≤ 650 → skipped entirely (loop breaks)
    Total is exactly 250 — never 250 plus a zero/negative second term.
    """
    rule = _multibuy_rule()
    ctx = _ctx([_line(30_000) for _ in range(3)] + [_line(20_000) for _ in range(3)])
    out = rule.calculate(ctx)
    assert out.discount_cents == 25_000
    # One group only — the explanation must not claim two.
    assert "1 group(s)" in out.explanation


def test_multibuy_partial_guard_affected_ids_exclude_the_skipped_group():
    """The skipped group's products are not reported as discounted."""
    dear = [uuid4() for _ in range(3)]
    cheap = [uuid4() for _ in range(3)]
    rule = _multibuy_rule()
    ctx = _ctx(
        [_line(30_000, product_id=pid) for pid in dear]
        + [_line(20_000, product_id=pid) for pid in cheap]
    )
    out = rule.calculate(ctx)
    assert set(out.affected_line_item_ids) == set(dear)
    assert not set(out.affected_line_item_ids) & set(cheap)


# --------------------------------------------------------------------------- #
# B. Validation — the JSONB persistence contract (no Alembic migration)       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # neither field
        {"multibuy_quantity": N},  # price missing
        {"multibuy_price_cents": P},  # quantity missing
    ],
    ids=["neither", "quantity_only", "price_only"],
)
def test_multibuy_requires_both_fields(kwargs):
    with pytest.raises(ValidationError) as exc:
        DiscountRule(kind=DiscountRuleKind.MULTIBUY, **kwargs)
    assert "multibuy" in str(exc.value)


def test_multibuy_quantity_of_one_is_rejected():
    """N=1 is a per-unit fixed price, not a bundle — ge=2."""
    with pytest.raises(ValidationError):
        DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_quantity=1,
            multibuy_price_cents=P,
        )


def test_multibuy_quantity_two_is_the_boundary_and_is_accepted():
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_quantity=2,
        multibuy_price_cents=P,
    )
    assert rule.multibuy_quantity == 2


def test_multibuy_price_zero_is_rejected():
    """A free bundle must be modelled as 100% off — gt=0."""
    with pytest.raises(ValidationError):
        DiscountRule(
            kind=DiscountRuleKind.MULTIBUY,
            multibuy_quantity=N,
            multibuy_price_cents=0,
        )


def test_multibuy_price_one_cent_is_the_boundary_and_is_accepted():
    rule = DiscountRule(
        kind=DiscountRuleKind.MULTIBUY,
        multibuy_quantity=N,
        multibuy_price_cents=1,
    )
    assert rule.multibuy_price_cents == 1


def test_multibuy_rule_round_trips_through_json():
    """Rules live in a JSONB column — dump/reload must be lossless."""
    rule = _multibuy_rule(min_subtotal_cents=50_000, max_discount_cents=30_000)
    dumped = rule.model_dump(mode="json")
    assert dumped["kind"] == "multibuy"
    assert dumped["multibuy_quantity"] == N
    assert dumped["multibuy_price_cents"] == P

    reparsed = DiscountRule.model_validate(dumped)
    assert reparsed == rule
    # And the reloaded rule still computes the same number.
    ctx = _ctx([_line(UNIT) for _ in range(3)])
    assert reparsed.calculate(ctx).discount_cents == rule.calculate(ctx).discount_cents


def test_multibuy_rule_round_trips_through_python_dump():
    rule = _multibuy_rule()
    assert DiscountRule.model_validate(rule.model_dump()) == rule


# --------------------------------------------------------------------------- #
# C. Shared modifiers — caps and minimums                                     #
# --------------------------------------------------------------------------- #


def test_multibuy_respects_max_discount_cents():
    """6 units raw 20000, capped to 15000."""
    rule = _multibuy_rule(max_discount_cents=15_000)
    ctx = _ctx([_line(UNIT) for _ in range(6)])
    assert rule.calculate(ctx).discount_cents == 15_000


def test_multibuy_uncapped_control_for_the_cap_test():
    rule = _multibuy_rule()
    ctx = _ctx([_line(UNIT) for _ in range(6)])
    assert rule.calculate(ctx).discount_cents == 20_000


def test_multibuy_min_subtotal_above_cart_yields_zero():
    rule = _multibuy_rule(min_subtotal_cents=100_000)
    ctx = _ctx([_line(UNIT) for _ in range(3)])  # subtotal 75000
    out = rule.calculate(ctx)
    assert out.discount_cents == 0
    assert "below minimum" in out.explanation


def test_multibuy_min_subtotal_exactly_met_applies():
    rule = _multibuy_rule(min_subtotal_cents=75_000)
    ctx = _ctx([_line(UNIT) for _ in range(3)])
    assert rule.calculate(ctx).discount_cents == 10_000


# --------------------------------------------------------------------------- #
# D. Scoping via role-tagged targets (through the calculator)                 #
# --------------------------------------------------------------------------- #


def _target(
    promo: Promotion,
    kind: TargetKind,
    value: dict,
    role: str | None,
) -> PromotionTarget:
    return PromotionTarget(
        tenant_id=promo.tenant_id,
        promotion_id=promo.id,
        target_kind=kind,
        target_value=value,
        inclusion=True,
        role=role,
    )


def test_multibuy_category_buy_set_blocks_when_eligible_units_short():
    """2 eligible + 2 ineligible units → no trio → zero discount."""
    eligible_cat, other_cat = uuid4(), uuid4()
    promo = _promo(PromotionSurface.AUTOMATIC, _multibuy_rule())
    targets = {
        promo.id: [
            _target(
                promo,
                TargetKind.CATEGORY,
                {"category_ids": [str(eligible_cat)]},
                "buy_set",
            )
        ]
    }
    lines = [_line(UNIT, category_id=eligible_cat) for _ in range(2)] + [
        _line(40_000, category_id=other_cat) for _ in range(2)
    ]
    res = DiscountCalculator().calculate_total(
        [promo], [], _ctx(lines), targets_by_promotion=targets
    )
    assert res.automatic_discount_cents == 0
    assert promo.id not in res.applied_promotion_ids


def test_multibuy_category_buy_set_scopes_the_group():
    """3 eligible + 2 (more expensive) ineligible → exactly 10000.

    If the ineligible units leaked into the group the answer would be
    40000 (two 400s + one 250 = 1050 − 650), so this number proves the
    filter held.
    """
    eligible_cat, other_cat = uuid4(), uuid4()
    promo = _promo(PromotionSurface.AUTOMATIC, _multibuy_rule())
    targets = {
        promo.id: [
            _target(
                promo,
                TargetKind.CATEGORY,
                {"category_ids": [str(eligible_cat)]},
                "buy_set",
            )
        ]
    }
    lines = [_line(UNIT, category_id=eligible_cat) for _ in range(3)] + [
        _line(40_000, category_id=other_cat) for _ in range(2)
    ]
    res = DiscountCalculator().calculate_total(
        [promo], [], _ctx(lines), targets_by_promotion=targets
    )
    assert res.automatic_discount_cents == 10_000
    assert res.applied_promotion_ids == [promo.id]


def test_multibuy_product_buy_set_scopes_the_group():
    """PRODUCT-kind buy_set works the same way as CATEGORY."""
    eligible_pid = uuid4()
    promo = _promo(PromotionSurface.AUTOMATIC, _multibuy_rule())
    targets = {
        promo.id: [
            _target(
                promo,
                TargetKind.PRODUCT,
                {"product_ids": [str(eligible_pid)]},
                "buy_set",
            )
        ]
    }
    lines = [
        _line(UNIT, qty=3, product_id=eligible_pid),
        _line(40_000),  # different product — must not join the trio
    ]
    res = DiscountCalculator().calculate_total(
        [promo], [], _ctx(lines), targets_by_promotion=targets
    )
    assert res.automatic_discount_cents == 10_000


def test_multibuy_untagged_target_is_not_a_line_filter():
    """A `role=None` target is an ELIGIBILITY gate, not a line filter.

    The calculator ignores it, so the rule applies to every line. (Whether
    the promotion runs at all is the eligibility checker's decision — see
    `PromotionEligibilityChecker`.)
    """
    unrelated_cat = uuid4()
    promo = _promo(PromotionSurface.AUTOMATIC, _multibuy_rule())
    targets = {
        promo.id: [
            _target(
                promo,
                TargetKind.CATEGORY,
                {"category_ids": [str(unrelated_cat)]},
                None,
            )
        ]
    }
    # None of these lines is in `unrelated_cat`.
    lines = [_line(UNIT, category_id=uuid4()) for _ in range(3)]
    res = DiscountCalculator().calculate_total(
        [promo], [], _ctx(lines), targets_by_promotion=targets
    )
    assert res.automatic_discount_cents == 10_000


def test_multibuy_unscoped_when_no_targets_map_supplied():
    promo = _promo(PromotionSurface.AUTOMATIC, _multibuy_rule())
    lines = [_line(UNIT) for _ in range(3)]
    res = DiscountCalculator().calculate_total([promo], [], _ctx(lines))
    assert res.automatic_discount_cents == 10_000


def test_multibuy_ignores_the_get_set_role():
    """MULTIBUY reads `buy_set` only; a stray `get_set` changes nothing."""
    eligible_cat, stray_cat = uuid4(), uuid4()
    promo = _promo(PromotionSurface.AUTOMATIC, _multibuy_rule())
    targets = {
        promo.id: [
            _target(
                promo,
                TargetKind.CATEGORY,
                {"category_ids": [str(eligible_cat)]},
                "buy_set",
            ),
            _target(
                promo,
                TargetKind.CATEGORY,
                {"category_ids": [str(stray_cat)]},
                "get_set",
            ),
        ]
    }
    lines = [_line(UNIT, category_id=eligible_cat) for _ in range(3)]
    res = DiscountCalculator().calculate_total(
        [promo], [], _ctx(lines), targets_by_promotion=targets
    )
    assert res.automatic_discount_cents == 10_000


# --------------------------------------------------------------------------- #
# E. Stacking — inherited from the engine, must not regress                   #
# --------------------------------------------------------------------------- #


def test_multibuy_stacks_with_a_code_promo():
    """§4 "pay 500" row: 750 cart, 20% code + the trio offer.

    Code is computed on the full 75000 subtotal → 15000. The automatic
    then runs against the remaining subtotal → 10000. Customer pays 50000.
    """
    code = _promo(
        PromotionSurface.DISCOUNT_CODE,
        DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=20),
    )
    auto = _promo(PromotionSurface.AUTOMATIC, _multibuy_rule())
    ctx = _ctx([_line(UNIT) for _ in range(3)])

    res = DiscountCalculator().calculate_total([code, auto], [], ctx)

    assert res.code_discount_cents == 15_000
    assert res.automatic_discount_cents == 10_000
    assert res.total_discount_cents == 25_000
    assert ctx.subtotal_cents - res.total_discount_cents == 50_000


# --------------------------------------------------------------------------- #
# G. Unknown rule kinds on old persisted data                                 #
# --------------------------------------------------------------------------- #


def test_unknown_kind_is_rejected_at_parse_time():
    """Documents ACTUAL behavior: pydantic refuses, it does not degrade.

    `PromotionMapper.promotion_to_entity` calls
    `DiscountRule.model_validate(m.discount_rule)` with no try/except, so a
    persisted rule whose `kind` is not in the enum raises before the engine
    is ever reached — see the report note on blast radius.
    """
    with pytest.raises(ValidationError):
        DiscountRule.model_validate({"kind": "quantity_break", "value_cents": 100})

    with pytest.raises(ValidationError):
        DiscountRule(kind="quantity_break", value_cents=100)


def test_engine_returns_zero_if_an_unknown_kind_ever_reaches_it():
    """The `calculate()` fall-through is defensive; prove it is inert.

    Validation is bypassed deliberately here (the only way to reach this
    branch today) so that the safety net itself is covered: no exception,
    no discount, and an explanation the merchant can read in the rejected
    list.
    """
    rule = DiscountRule(kind=DiscountRuleKind.FIXED, value_cents=100)
    object.__setattr__(rule, "kind", "quantity_break")

    out = rule.calculate(_ctx([_line(UNIT) for _ in range(3)]))

    assert out.discount_cents == 0
    assert out.free_shipping is False
    assert out.explanation == "unknown rule kind"

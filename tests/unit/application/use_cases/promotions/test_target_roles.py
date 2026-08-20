"""Multibuy scope is promoted to `buy_set` on write.

The failure this prevents is invisible from the merchant UI, which has no
"role" control at all: untagged catalog targets are eligibility GATES, so a
"any 2 caps for 968" built the obvious way both hides itself from every
empty-cart shopper AND forms its group from any two cart lines once it does
appear. See `use_cases.promotions.target_roles` for the full reasoning.
"""

from uuid import uuid4

from src.application.use_cases.promotions.target_roles import normalize_target_roles
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import TargetKind
from src.core.value_objects.discount_rule import (
    BundleLeg,
    DiscountRule,
    DiscountRuleKind,
    MultibuyTier,
)

CAPS = uuid4()
TEES = uuid4()

MULTIBUY = DiscountRule(
    kind=DiscountRuleKind.MULTIBUY,
    multibuy_tiers=[MultibuyTier(quantity=2, price_cents=96_800)],
)
PERCENTAGE = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10)
BUNDLE = DiscountRule(
    kind=DiscountRuleKind.BUNDLE,
    bundle_legs=[BundleLeg(), BundleLeg()],
    bundle_price_cents=109_100,
)


def _target(
    kind: TargetKind = TargetKind.CATEGORY,
    *,
    inclusion: bool = True,
    role: str | None = None,
    value: dict | None = None,
) -> PromotionTarget:
    return PromotionTarget(
        tenant_id=uuid4(),
        promotion_id=uuid4(),
        target_kind=kind,
        target_value=value if value is not None else {"category_ids": [str(CAPS)]},
        inclusion=inclusion,
        role=role,
    )


def test_untagged_category_scope_becomes_a_buy_set():
    out = normalize_target_roles(MULTIBUY, [_target()])
    assert [t.role for t in out] == ["buy_set"]


def test_untagged_product_scope_becomes_a_buy_set():
    out = normalize_target_roles(
        MULTIBUY,
        [_target(TargetKind.PRODUCT, value={"product_ids": [str(uuid4())]})],
    )
    assert [t.role for t in out] == ["buy_set"]


def test_an_explicit_buy_set_is_never_second_guessed():
    """A client that already tags its rows keeps full control of the rest."""
    tagged = _target(role="buy_set")
    gate = _target(value={"category_ids": [str(TEES)]})
    out = normalize_target_roles(MULTIBUY, [tagged, gate])
    assert [t.role for t in out] == ["buy_set", None]


def test_exclusions_stay_eligibility_gates():
    """ "...but not clearance" is a real gate, and inverting it changes the offer."""
    out = normalize_target_roles(MULTIBUY, [_target(inclusion=False)])
    assert [t.role for t in out] == [None]


def test_audience_rows_are_left_alone():
    out = normalize_target_roles(
        MULTIBUY, [_target(TargetKind.AUDIENCE, value={"segment": "vip"})]
    )
    assert [t.role for t in out] == [None]


def test_percentage_rules_keep_their_gates():
    """ "10% off when the cart has a cap" is coherent — do not rewrite it."""
    out = normalize_target_roles(PERCENTAGE, [_target()])
    assert [t.role for t in out] == [None]


def test_bundle_legs_are_never_inferred():
    """Nothing in an untagged row says WHICH leg it is; guessing misprices."""
    out = normalize_target_roles(BUNDLE, [_target()])
    assert [t.role for t in out] == [None]


def test_no_rule_is_a_no_op():
    targets = [_target()]
    assert normalize_target_roles(None, targets) is targets


def test_nothing_to_promote_returns_the_same_objects():
    targets = [_target(role="buy_set")]
    assert normalize_target_roles(MULTIBUY, targets) is targets


def test_inputs_are_not_mutated():
    original = _target()
    normalize_target_roles(MULTIBUY, [original])
    assert original.role is None

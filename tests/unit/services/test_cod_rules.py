"""Which COD orders the OTP and the deposit apply to, and the prepaid
incentive."""

from uuid import uuid4

import pytest

from src.application.services.cod_rules import (
    CodRules,
    OrderConditions,
    OrderFacts,
    PrepaidIncentive,
    applies,
    get_cod_rules,
    prepaid_incentive,
)

SHOES, SALE = uuid4(), uuid4()


def facts(**kw):
    base = {
        "is_cod": True,
        "subtotal_cents": 50_000,
        "first_time": False,
        "high_risk": False,
    }
    return OrderFacts(**(base | kw))


def test_everyone_covers_every_cod_order_and_no_prepaid_one():
    rule = OrderConditions()
    assert applies(rule, facts()) is True
    assert applies(rule, facts(is_cod=False)) is False


@pytest.mark.parametrize(
    ("rule", "order", "expected"),
    [
        (
            OrderConditions(everyone=False, first_time=True),
            facts(first_time=True),
            True,
        ),
        (OrderConditions(everyone=False, first_time=True), facts(), False),
        (
            OrderConditions(everyone=False, min_order_cents=100_000),
            facts(subtotal_cents=100_000),
            True,
        ),
        (
            OrderConditions(everyone=False, min_order_cents=100_000),
            facts(subtotal_cents=99_999),
            False,
        ),
        (OrderConditions(everyone=False, high_risk=True), facts(high_risk=True), True),
        (
            OrderConditions(everyone=False, product_ids=[SHOES]),
            facts(product_ids=frozenset({SHOES})),
            True,
        ),
        (
            OrderConditions(everyone=False, category_ids=[SALE]),
            facts(category_ids=frozenset({SALE})),
            True,
        ),
        (
            OrderConditions(everyone=False, category_ids=[SALE]),
            facts(category_ids=frozenset({uuid4()})),
            False,
        ),
        # Nothing switched on: no order matches.
        (
            OrderConditions(everyone=False),
            facts(first_time=True, high_risk=True),
            False,
        ),
    ],
)
def test_conditions_match_on_any_switched_on_one(rule, order, expected):
    assert applies(rule, order) is expected


def test_prepaid_percent_is_on_what_is_left_after_other_discounts():
    rule = PrepaidIncentive(enabled=True, kind="percent", percent=5)
    out = prepaid_incentive(
        rule, is_cod=False, subtotal_cents=100_000, discount_cents=20_000
    )
    assert out.discount_cents == 4_000 and out.free_shipping is False


def test_prepaid_fixed_never_exceeds_the_rest_of_the_subtotal():
    rule = PrepaidIncentive(enabled=True, kind="fixed", amount_cents=5_000)
    out = prepaid_incentive(rule, is_cod=False, subtotal_cents=3_000, discount_cents=0)
    assert out.discount_cents == 3_000


def test_prepaid_free_shipping():
    rule = PrepaidIncentive(enabled=True, kind="free_shipping")
    assert prepaid_incentive(
        rule, is_cod=False, subtotal_cents=1, discount_cents=0
    ).free_shipping


@pytest.mark.parametrize(
    ("rule", "is_cod", "subtotal"),
    [
        (PrepaidIncentive(enabled=False), False, 100_000),
        (PrepaidIncentive(enabled=True), True, 100_000),
        (PrepaidIncentive(enabled=True, min_order_cents=50_000), False, 49_999),
    ],
)
def test_no_incentive_when_off_cod_or_below_the_minimum(rule, is_cod, subtotal):
    out = prepaid_incentive(
        rule, is_cod=is_cod, subtotal_cents=subtotal, discount_cents=0
    )
    assert out.discount_cents == 0 and out.free_shipping is False


def test_missing_or_damaged_rules_mean_the_old_behaviour():
    assert get_cod_rules({}) == CodRules()
    assert get_cod_rules({"cod_rules": {"otp": {"min_order_cents": -5}}}) == CodRules()
    saved = get_cod_rules({
        "cod_rules": {"otp": {"everyone": False, "first_time": True}}
    })
    assert saved.otp.first_time is True and saved.deposit.everyone is True

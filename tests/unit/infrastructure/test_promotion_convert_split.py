"""The convert handler's discount split.

Every convert event used to store a zero discount, so the merchant's offer
page reported "0 revenue / 0 discount" for an offer that had sold all month.
These pin the arithmetic that fills it in.
"""

from src.infrastructure.events.handlers.promotion_convert_handler import (
    split_order_discounts,
)


def test_code_only_order_attributes_the_whole_discount_to_the_code():
    auto, code = split_order_discounts([], 5000)
    assert auto == {}
    assert code == 5000


def test_automatic_promotions_take_their_own_share_first():
    auto, code = split_order_discounts(
        [{"id": "p1", "amount": 1500}, {"id": "p2", "amount": 500}], 3000
    )
    assert auto == {"p1": 1500, "p2": 500}
    # The code's share is the remainder, not the order's whole discount.
    assert code == 1000


def test_fully_automatic_order_leaves_no_code_share():
    auto, code = split_order_discounts([{"id": "p1", "amount": 2000}], 2000)
    assert auto == {"p1": 2000}
    assert code == 0


def test_never_reports_a_negative_code_share():
    # Belt and braces: a rounding slip between the order's stored total and
    # its promotion rows must not write a negative discount into analytics.
    _, code = split_order_discounts([{"id": "p1", "amount": 2500}], 2000)
    assert code == 0


def test_malformed_rows_are_skipped_not_raised_on():
    auto, code = split_order_discounts(
        [{"amount": 100}, {"id": "p1", "amount": "x"}, {"id": "p2", "amount": 700}],
        1000,
    )
    assert auto == {"p2": 700}
    assert code == 300


def test_missing_values_are_treated_as_zero():
    assert split_order_discounts(None, None) == ({}, 0)

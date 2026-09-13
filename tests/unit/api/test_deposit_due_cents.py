"""Sizing rules for the COD deposit-to-confirm policy.

The deposit is money, and it is computed twice — once by the storefront to
quote the customer, once by checkout to charge them. Both read this function,
so a change here has to keep the quote and the charge agreeing.
"""

from src.api.v1.schemas.tenant.settings import deposit_due_cents

FIXED = {"enabled": True, "mode": "fixed", "amount_cents": 5_000}
HALF = {"enabled": True, "mode": "percent", "percent": 50}


def test_fixed_amount_is_charged_as_configured() -> None:
    assert deposit_due_cents(FIXED, 30_000) == 5_000


def test_disabled_policy_never_charges() -> None:
    assert deposit_due_cents({**FIXED, "enabled": False}, 30_000) == 0
    assert deposit_due_cents(None, 30_000) == 0
    assert deposit_due_cents({}, 30_000) == 0


def test_threshold_gates_small_orders() -> None:
    policy = {**FIXED, "min_order_cents": 50_000}
    assert deposit_due_cents(policy, 49_999) == 0
    # Compared with >=, so an order landing exactly on the threshold pays.
    assert deposit_due_cents(policy, 50_000) == 5_000


def test_percent_takes_a_share_of_the_order() -> None:
    assert deposit_due_cents(HALF, 30_000) == 15_000
    assert deposit_due_cents({**HALF, "percent": 25}, 30_000) == 7_500


def test_percent_rounds_to_nearest_cent() -> None:
    # Truncating an odd total would quietly short the merchant a cent.
    assert deposit_due_cents(HALF, 30_001) == 15_001
    assert deposit_due_cents({**HALF, "percent": 33}, 100) == 33


def test_deposit_never_exceeds_the_order() -> None:
    assert deposit_due_cents({**FIXED, "amount_cents": 99_999}, 30_000) == 30_000


def test_empty_order_owes_nothing() -> None:
    assert deposit_due_cents(HALF, 0) == 0
    assert deposit_due_cents(FIXED, 0) == 0

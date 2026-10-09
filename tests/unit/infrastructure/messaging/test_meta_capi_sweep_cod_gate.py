"""The orphan sweep stands down for COD orders whose timing a status trigger owns.

Regression for a live double count: a COD order's ``paid_at`` is stamped when
the cash is collected on delivery, days after placement. The sweep gated only
on ``paid_at IS NULL``, so a trigger store's COD order whose confirmation-page
Purchase had been lost got a second Purchase at cash collection, outside Meta's
48-hour dedup window.
"""

from types import SimpleNamespace

from src.infrastructure.messaging.tasks.meta_capi import _trigger_owns_cod_purchase

_TRIGGER = {"purchase_trigger": "confirmed"}


def _order(payment_method, paid_at=None):
    return SimpleNamespace(payment_method=payment_method, paid_at=paid_at)


def test_cod_order_with_cash_collected_is_left_to_the_trigger():
    assert _trigger_owns_cod_purchase(_order("cod", paid_at="2026-09-29"), _TRIGGER)


def test_cod_order_before_collection_is_left_to_the_trigger():
    assert _trigger_owns_cod_purchase(_order("cod"), _TRIGGER)


def test_missing_payment_method_counts_as_cod():
    assert _trigger_owns_cod_purchase(_order(None, paid_at="2026-09-29"), _TRIGGER)


def test_gateway_paid_order_is_still_recovered():
    assert not _trigger_owns_cod_purchase(
        _order("card", paid_at="2026-09-29"), _TRIGGER
    )


def test_store_without_trigger_keeps_the_sweep_as_backstop():
    assert not _trigger_owns_cod_purchase(_order("cod", paid_at="2026-09-29"), {})
    assert not _trigger_owns_cod_purchase(_order("cod"), {"purchase_trigger": None})


def test_trigger_outside_the_valid_set_does_not_silence_the_sweep():
    assert not _trigger_owns_cod_purchase(_order("cod"), {"purchase_trigger": "paid"})

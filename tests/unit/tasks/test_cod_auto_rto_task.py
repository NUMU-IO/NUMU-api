"""Unit tests for the cod_auto_rto_task helper functions.

The full sweep coroutine is hard to unit-test without spinning up a DB
session and Celery infrastructure. We exercise the small pure-function
helpers that drive per-store decisions: settings parsing, threshold
clamping, and opt-out detection.
"""

from __future__ import annotations

import pytest

from src.infrastructure.messaging.tasks.cod_auto_rto_task import (
    _DEFAULT_AUTO_RTO_DAYS,
    _MAX_AUTO_RTO_DAYS,
    _MIN_AUTO_RTO_DAYS,
    _is_auto_rto_disabled,
    _resolve_auto_rto_days,
)

# ─── _resolve_auto_rto_days ───────────────────────────────────────────


def test_no_settings_returns_default():
    assert _resolve_auto_rto_days(None) == _DEFAULT_AUTO_RTO_DAYS
    assert _resolve_auto_rto_days({}) == _DEFAULT_AUTO_RTO_DAYS


def test_no_cod_trust_block_returns_default():
    assert _resolve_auto_rto_days({"other_setting": True}) == _DEFAULT_AUTO_RTO_DAYS


def test_explicit_days_within_range_used_as_is():
    assert _resolve_auto_rto_days({"cod_trust": {"auto_rto_days": 21}}) == 21


def test_below_minimum_clamps_to_seven():
    assert (
        _resolve_auto_rto_days({"cod_trust": {"auto_rto_days": 3}})
        == _MIN_AUTO_RTO_DAYS
    )
    assert _MIN_AUTO_RTO_DAYS == 7


def test_above_maximum_clamps_to_sixty():
    assert (
        _resolve_auto_rto_days({"cod_trust": {"auto_rto_days": 365}})
        == _MAX_AUTO_RTO_DAYS
    )
    assert _MAX_AUTO_RTO_DAYS == 60


def test_non_int_value_falls_back_to_default():
    assert (
        _resolve_auto_rto_days({"cod_trust": {"auto_rto_days": "thirty"}})
        == _DEFAULT_AUTO_RTO_DAYS
    )
    assert (
        _resolve_auto_rto_days({"cod_trust": {"auto_rto_days": None}})
        == _DEFAULT_AUTO_RTO_DAYS
    )


def test_malformed_cod_trust_block_returns_default():
    """A non-dict value where we expected a dict shouldn't crash the sweep."""
    assert _resolve_auto_rto_days({"cod_trust": "not_a_dict"}) == _DEFAULT_AUTO_RTO_DAYS


# ─── _is_auto_rto_disabled ────────────────────────────────────────────


def test_no_settings_means_enabled():
    assert _is_auto_rto_disabled(None) is False
    assert _is_auto_rto_disabled({}) is False


def test_explicit_disable():
    assert _is_auto_rto_disabled({"cod_trust": {"auto_rto_disabled": True}}) is True


def test_explicit_enable():
    assert _is_auto_rto_disabled({"cod_trust": {"auto_rto_disabled": False}}) is False


def test_truthy_non_bool_disable():
    """Defensive: a non-bool truthy value still disables."""
    assert _is_auto_rto_disabled({"cod_trust": {"auto_rto_disabled": "yes"}}) is True


def test_malformed_cod_trust_block_does_not_disable():
    assert _is_auto_rto_disabled({"cod_trust": "not_a_dict"}) is False


# ─── Default constants ────────────────────────────────────────────────


def test_defaults_are_sane():
    """Sanity-check the constants that gate merchant behaviour."""
    assert _DEFAULT_AUTO_RTO_DAYS == 14
    assert _MIN_AUTO_RTO_DAYS <= _DEFAULT_AUTO_RTO_DAYS <= _MAX_AUTO_RTO_DAYS


@pytest.mark.parametrize(
    "raw_days,expected",
    [
        (7, 7),
        (8, 8),
        (14, 14),
        (30, 30),
        (60, 60),
        (61, 60),  # clamped
        (6, 7),  # clamped
        (-5, 7),  # clamped
    ],
)
def test_resolve_days_parametrized(raw_days, expected):
    assert (
        _resolve_auto_rto_days({"cod_trust": {"auto_rto_days": raw_days}}) == expected
    )

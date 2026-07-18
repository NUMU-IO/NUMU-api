"""Unit tests for COD Autopilot pure logic (004-cod-autopilot).

Covers the settings reader (defaults + clamps, FR-022), the store-local
clock (R-02), delivery-check attempt bookkeeping (FR-012), and the
payload locator convention — the pieces every sweep decision rests on.
DB-backed flows are exercised in integration/staging per quickstart.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.services.cod_autopilot_service import (
    COD_AUTOPILOT_DEFAULTS,
    DIGEST_MAX_ORDERS,
    CodAutopilotConfig,
    _exhaust,
    _locator,
    _record_attempt,
    get_cod_autopilot_settings,
    store_local_now,
)

# ─── settings reader ──────────────────────────────────────────────────


def test_defaults_when_section_absent():
    for settings in (None, {}, {"other": 1}, {"cod_autopilot": "not_a_dict"}):
        config = get_cod_autopilot_settings(settings)
        assert config.enabled is False
        assert config.digest_hour == 18
        assert config.delivery_check_delay_days == 3
        assert config.delivery_check_retry_days == 2
        assert config.delivery_check_max_attempts == 3
        assert config.assumed_delivered_days == 10


def test_defaults_dict_matches_config_fields():
    config = get_cod_autopilot_settings({})
    for key, value in COD_AUTOPILOT_DEFAULTS.items():
        assert getattr(config, key) == value


def test_explicit_values_used():
    config = get_cod_autopilot_settings({
        "cod_autopilot": {
            "enabled": True,
            "digest_hour": 9,
            "delivery_check_delay_days": 5,
            "delivery_check_retry_days": 1,
            "delivery_check_max_attempts": 2,
            "assumed_delivered_days": 21,
        }
    })
    assert config == CodAutopilotConfig(
        enabled=True,
        digest_hour=9,
        delivery_check_delay_days=5,
        delivery_check_retry_days=1,
        delivery_check_max_attempts=2,
        assumed_delivered_days=21,
    )


@pytest.mark.parametrize(
    "key,raw,expected",
    [
        ("digest_hour", -1, 0),
        ("digest_hour", 99, 23),
        ("delivery_check_delay_days", 0, 1),
        ("delivery_check_delay_days", 30, 7),
        ("delivery_check_retry_days", 0, 1),
        ("delivery_check_max_attempts", 10, 3),
        ("delivery_check_max_attempts", 0, 1),
        ("assumed_delivered_days", 1, 5),
        ("assumed_delivered_days", 365, 30),
        ("assumed_delivered_days", "ten", 10),  # non-int → default
    ],
)
def test_bounds_clamped(key, raw, expected):
    config = get_cod_autopilot_settings({"cod_autopilot": {key: raw}})
    assert getattr(config, key) == expected


def test_enabled_defaults_off_fr002():
    """FR-002: Autopilot is off unless the merchant explicitly enables it."""
    assert get_cod_autopilot_settings({"cod_autopilot": {}}).enabled is False


# ─── store-local clock (R-02) ─────────────────────────────────────────


def test_store_local_now_egypt_offset():
    utc_noon = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    local = store_local_now("EG", utc_noon)
    # Cairo is UTC+3 in July (DST reinstated 2023).
    assert local.hour in (14, 15)
    assert local.utcoffset() != timedelta(0)


def test_store_local_now_saudi_offset():
    utc_noon = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    local = store_local_now("SA", utc_noon)
    assert local.hour == 15  # Riyadh is UTC+3, no DST


def test_store_local_now_unknown_country_falls_back_to_egypt():
    utc_noon = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    assert store_local_now("XX", utc_noon).hour == store_local_now("EG", utc_noon).hour
    assert store_local_now(None, utc_noon).hour == store_local_now("EG", utc_noon).hour


# ─── attempt bookkeeping (FR-012) ─────────────────────────────────────


def _row(**overrides):
    base = {
        "attempts": 0,
        "max_attempts": 3,
        "first_sent_at": None,
        "last_sent_at": None,
        "next_attempt_at": None,
        "outcome": "pending",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_CONFIG = CodAutopilotConfig(
    enabled=True,
    digest_hour=18,
    delivery_check_delay_days=3,
    delivery_check_retry_days=2,
    delivery_check_max_attempts=3,
    assumed_delivered_days=10,
)


def test_record_attempt_schedules_retry_until_max():
    now = datetime.now(UTC)
    row = _row()
    _record_attempt(row, now, _CONFIG)
    assert row.attempts == 1
    assert row.first_sent_at == now
    assert row.last_sent_at == now
    assert row.next_attempt_at == now + timedelta(days=2)

    later = now + timedelta(days=2)
    _record_attempt(row, later, _CONFIG)
    assert row.attempts == 2
    assert row.first_sent_at == now  # unchanged
    assert row.next_attempt_at == later + timedelta(days=2)


def test_record_attempt_final_attempt_stops_scheduling():
    """Total attempts never exceed max (initial + retries, FR-012)."""
    now = datetime.now(UTC)
    row = _row(attempts=2)
    _record_attempt(row, now, _CONFIG)
    assert row.attempts == 3
    assert row.next_attempt_at is None


def test_exhaust_clears_scheduling():
    now = datetime.now(UTC)
    row = _row(next_attempt_at=now)
    _exhaust(row, now)
    assert row.outcome == "response_exhausted"
    assert row.next_attempt_at is None


# ─── payload locator convention ───────────────────────────────────────


def test_locator_prefers_subdomain():
    oid = uuid4()
    assert _locator("cairostyle", oid) == f"cairostyle/{oid}"
    assert _locator(None, oid) == str(oid)
    assert _locator("", oid) == str(oid)


def test_digest_cap_is_ten():
    """R-09: Meta body-param limits cap the digest at 10 listed orders."""
    assert DIGEST_MAX_ORDERS == 10

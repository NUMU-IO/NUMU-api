from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.core.entitlements import (
    UNLIMITED,
    Feature,
    Flag,
    FlagTarget,
    Grant,
    bucket,
    check_value,
    flag_on,
    next_change,
    resolve,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
DAY = timedelta(days=1)
ANALYTICS = Feature("advanced_analytics", "boolean", False)
STAFF = Feature("staff_accounts", "limit", 1)


def plan(value, key="starter"):
    return Grant("plan", key, value)


def addon(value, slug="analytics_plus", ends=NOW + 10 * DAY):
    return Grant("addon", slug, value, expires_at=ends)


def override(value, starts=None, ends=None):
    return Grant("override", "ovr-1", value, starts_at=starts, expires_at=ends)


def test_plan_grants():
    r = resolve(ANALYTICS, bundles=[plan(True, "pro")], override=None, now=NOW)
    assert (r.available, r.source, r.source_id, r.expires_at) == (
        True,
        "plan",
        "pro",
        None,
    )


def test_override_denies_and_shows_what_it_shadowed():
    r = resolve(
        ANALYTICS, bundles=[plan(True, "pro")], override=override(False), now=NOW
    )
    assert (r.available, r.reason, r.source) == (False, "blocked", "override")
    assert r.shadowed == (plan(True, "pro"),)


def test_override_only_counts_inside_its_window():
    for window in (override(True, ends=NOW), override(True, starts=NOW + DAY)):
        r = resolve(ANALYTICS, bundles=[plan(False)], override=window, now=NOW)
        assert (r.available, r.source, r.reason) == (False, "plan", "not_in_plan")
    live = override(True, starts=NOW - DAY, ends=NOW + 30 * DAY)
    r = resolve(ANALYTICS, bundles=[plan(False)], override=live, now=NOW)
    assert (r.available, r.source, r.expires_at) == (True, "override", NOW + 30 * DAY)


def test_addon_grants_until_it_ends_unless_the_plan_also_does():
    r = resolve(ANALYTICS, bundles=[plan(False), addon(True)], override=None, now=NOW)
    assert (r.available, r.source, r.expires_at) == (True, "addon", NOW + 10 * DAY)
    r = resolve(
        ANALYTICS, bundles=[plan(True, "pro"), addon(True)], override=None, now=NOW
    )
    assert (r.source, r.expires_at) == ("plan", None)
    lapsed = addon(True, ends=NOW - DAY)
    r = resolve(ANALYTICS, bundles=[plan(False), lapsed], override=None, now=NOW)
    assert r.available is False


def test_limits_add_up_and_unlimited_absorbs():
    seats = addon(2, "extra_seats")
    r = resolve(STAFF, bundles=[plan(3), seats], override=None, now=NOW)
    assert (r.value, r.expires_at) == (5, seats.expires_at)
    r = resolve(STAFF, bundles=[plan(UNLIMITED, "pro"), seats], override=None, now=NOW)
    assert r.value == UNLIMITED
    r = resolve(STAFF, bundles=[plan(0)], override=None, now=NOW)
    assert (r.available, r.reason) == (False, "not_in_plan")


def test_override_is_absolute_not_additive():
    r = resolve(STAFF, bundles=[plan(10, "pro")], override=override(4), now=NOW)
    assert (r.value, r.source, r.shadowed) == (4, "override", (plan(10, "pro"),))


def test_default_when_nothing_grants():
    r = resolve(STAFF, bundles=[], override=None, now=NOW)
    assert (r.value, r.source, r.available) == (1, "default", True)


def test_kill_switch_gates_but_keeps_the_answer_visible():
    killed = Feature("advanced_analytics", "boolean", False, enabled=False)
    r = resolve(killed, bundles=[plan(True, "pro")], override=None, now=NOW)
    assert (r.available, r.reason, r.value, r.source) == (
        False,
        "disabled_globally",
        True,
        "plan",
    )


def test_values_are_type_checked():
    assert check_value("limit", UNLIMITED) == UNLIMITED
    assert check_value("limit", 0) == 0
    assert check_value("boolean", False) is False
    for kind, bad in (
        ("limit", -1),
        ("limit", True),
        ("limit", 2.5),
        ("boolean", 1),
        ("boolean", "true"),
    ):
        with pytest.raises(ValueError):
            check_value(kind, bad)


def test_snapshot_lives_until_the_next_boundary():
    grants = [
        override(True, starts=NOW + 5 * DAY, ends=NOW + 9 * DAY),
        addon(True, ends=NOW + 3 * DAY),
        addon(True, ends=NOW - DAY),
        plan(True),
    ]
    assert next_change(grants, NOW) == NOW + 3 * DAY
    assert next_change([plan(True)], NOW) is None


def test_bucket_is_stable_even_and_independent_per_flag():
    tenants = [str(uuid4()) for _ in range(20_000)]
    assert all(
        bucket("checkout_v2", t) == bucket("checkout_v2", t) for t in tenants[:50]
    )
    in_a = {t for t in tenants if bucket("checkout_v2", t) < 2_000}
    in_b = {t for t in tenants if bucket("analytics_v2", t) < 2_000}
    assert abs(len(in_a) / len(tenants) - 0.20) < 0.015
    # Independent flags overlap like independent coins (~4%), not ~20%.
    assert abs(len(in_a & in_b) / len(tenants) - 0.04) < 0.01


def test_raising_the_percentage_only_adds_tenants():
    tenants = [str(uuid4()) for _ in range(2_000)]
    on = lambda pct: {  # noqa: E731
        t for t in tenants if flag_on(Flag("checkout_v2", True, pct), t, None, NOW)[0]
    }
    assert on(10) <= on(25) <= on(60) <= on(100) == set(tenants)
    assert on(0) == set()


def test_flag_order():
    t = str(uuid4())
    beta = Flag("multi_warehouse_v1", True, 0)
    assert flag_on(None, t, None, NOW) == (False, "unknown_flag")
    assert flag_on(beta, t, None, NOW) == (False, "not_in_rollout")
    assert flag_on(beta, t, FlagTarget(True), NOW) == (True, "targeted")
    assert flag_on(beta, t, FlagTarget(True, NOW - DAY), NOW) == (
        False,
        "not_in_rollout",
    )
    # The master switch beats a target: it is the kill switch.
    off = Flag("multi_warehouse_v1", False, 100)
    assert flag_on(off, t, FlagTarget(True), NOW) == (False, "flag_off")
    # A target can hold one tenant back from a full rollout.
    everyone = Flag("checkout_v2", True, 100)
    assert flag_on(everyone, t, FlagTarget(False), NOW) == (False, "targeted")
    # Platform-level checks have no tenant: only "everyone" counts.
    assert flag_on(Flag("x", True, 50), None, None, NOW) == (False, "not_in_rollout")
    assert flag_on(everyone, None, None, NOW) == (True, "everyone")

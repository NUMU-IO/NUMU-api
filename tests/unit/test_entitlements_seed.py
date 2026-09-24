"""The entitlements migration seeds exactly what PLAN_LIMITS enforced, except
where a recorded decision changes it. This is the safety net for moving the
limits out of code: a transcription slip here would silently re-price a plan."""

import importlib.util
from pathlib import Path

from src.core.entities.plan import PLAN_LIMITS

_spec = importlib.util.spec_from_file_location(
    "entitlements_migration",
    Path(__file__).resolve().parents[2] / "alembic/versions/20260925_entitlements.py",
)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

#: The pro floor capability_service applied before the catalog existed.
PRO_AND_UP = {"pro", "developer", "enterprise"}


def _legacy(value):
    return "unlimited" if value == -1 else value


def test_seed_matches_plan_limits_except_recorded_decisions():
    rows = {(p, f): v for p, f, v in migration.seed_rows({})}
    assert set(PLAN_LIMITS) | {"beta"} == set(migration.PLANS)
    for plan, features in PLAN_LIMITS.items():
        for field, feature in migration.FIELD_TO_FEATURE.items():
            expected = migration.DECISIONS.get(
                (plan, feature), _legacy(getattr(features, field))
            )
            assert rows[(plan, feature)] == expected, (plan, field)
        assert rows[(plan, "partner_apps")] == _legacy(features.max_partner_apps)
        for feature in ("multi_warehouse", "product_subscriptions"):
            assert rows[(plan, feature)] is (plan in PRO_AND_UP), (plan, feature)


def test_beta_keeps_the_trial_grants_it_silently_had():
    rows = {(p, f): v for p, f, v in migration.seed_rows({})}
    for feature in migration.FIELD_TO_FEATURE.values():
        assert rows[("beta", feature)] == rows[("trial", feature)]


def test_admin_edits_layer_on_top_but_decisions_win():
    rows = {
        (p, f): v
        for p, f, v in migration.seed_rows({
            "starter": {"max_products": 100, "monthly_price_piasters": 1},
            "pro": {"max_staff_members": 15, "max_orders_per_month": -1},
        })
    }
    assert rows[("starter", "products")] == "unlimited"  # D1
    assert rows[("pro", "staff_accounts")] == 15
    assert rows[("pro", "orders_per_month")] == "unlimited"


def test_every_seeded_value_has_a_valid_shape():
    kinds = {f[0]: f[4] for f in migration.FEATURES}
    for _plan, feature, value in migration.seed_rows({}):
        if kinds[feature] == "boolean":
            assert type(value) is bool
        else:
            assert value == "unlimited" or (type(value) is int and value >= 0)

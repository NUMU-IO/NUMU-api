"""Abandoned-cart recovery becomes a Pro feature; every current store keeps it.

Revision ID: ent_abandoned_cart_20260925
Revises: ent_multi_warehouse_20260925
Create Date: 2026-09-24

Decision D5 in docs/entitlements-design.md. Recovery ran for every store:
the email fallback ignored the merchant's toggle. So every tenant that
exists today gets a permanent `migration` override, and only stores created
from now on need Pro (or an admin grant) for it. A new catalog key is
missing from every cached snapshot, which would read it as off until the
TTL, so every tenant's entitlements_version moves on.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ent_abandoned_cart_20260925"
down_revision: str | None = "ent_multi_warehouse_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The catalog row, for tests that seed the catalog without running SQL.
#: key, name, name_ar, category, kind, default, usage, period, enforcement
FEATURE = (
    "abandoned_cart",
    "Abandoned-cart recovery",
    "استرجاع السلات المتروكة",
    "marketing",
    "boolean",
    False,
    None,
    None,
    "hard",
)
PRO_AND_UP = ("pro", "developer", "enterprise")


def upgrade() -> None:
    op.execute(
        "INSERT INTO public.features (key, name, name_ar, category, kind,"
        " default_value, usage, period, enforcement) VALUES ('abandoned_cart',"
        " 'Abandoned-cart recovery', 'استرجاع السلات المتروكة', 'marketing',"
        " 'boolean', 'false', NULL, NULL, 'hard') ON CONFLICT (key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO public.plan_entitlements (plan_key, feature_key, value)"
        " SELECT DISTINCT plan_key, 'abandoned_cart',"
        "        CASE WHEN plan_key IN ('pro', 'developer', 'enterprise')"
        "             THEN 'true'::jsonb ELSE 'false'::jsonb END"
        " FROM public.plan_entitlements WHERE plan_key NOT LIKE 'addon:%'"
        " ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO public.entitlement_overrides"
        " (tenant_id, feature_key, value, source, reason)"
        " SELECT t.id, 'abandoned_cart', 'true'::jsonb, 'migration',"
        "        'grandfathered 2026-09: abandoned-cart recovery was on every plan'"
        " FROM public.tenants t"
        " WHERE NOT EXISTS ("
        "     SELECT 1 FROM public.plan_entitlements pe"
        "     WHERE pe.plan_key = t.plan AND pe.feature_key = 'abandoned_cart'"
        "       AND pe.value = 'true'::jsonb)"
        " ON CONFLICT DO NOTHING"
    )
    op.execute(
        "UPDATE public.tenants SET entitlements_version = entitlements_version + 1"
    )


def downgrade() -> None:
    op.execute("DELETE FROM public.features WHERE key = 'abandoned_cart'")

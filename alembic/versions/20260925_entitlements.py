"""Entitlements, release flags and usage counters.

Revision ID: entitlements_20260925
Revises: kashier_card_token_20260923
Create Date: 2026-09-24

Replaces the feature half of PLAN_LIMITS with a catalog the resolver reads
(docs/entitlements-design.md). The seed is a literal snapshot of PLAN_LIMITS
on 2026-09-24, not an import: a migration that imports app code breaks the
day that code changes. Admin edits made through /admin/plan-limits
(platform_config 'plan_limits') are layered on top, so prod keeps the values
it runs with today, except where a decision below changes them.

Decisions applied here:
  D1 (2026-09-24): Starter gets unlimited products; orders stay unlimited.
  D6: ``beta`` (written by public/beta.py, defined nowhere) gets trial's
      grants, which is what it silently got before.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "entitlements_20260925"
down_revision: str | None = "kashier_card_token_20260923"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

U = "unlimited"

# key, name, name_ar, category, kind, default, usage, period, enforcement
FEATURES = [
    (
        "products",
        "Products",
        "المنتجات",
        "catalog",
        "limit",
        100,
        "count",
        None,
        "hard",
    ),
    (
        "orders_per_month",
        "Orders per month",
        "الطلبات شهريًا",
        "orders",
        "limit",
        U,
        "count",
        "month",
        "soft",
    ),
    ("stores", "Stores", "المتاجر", "account", "limit", 1, "count", None, "hard"),
    (
        "staff_accounts",
        "Staff accounts",
        "حسابات الموظفين",
        "account",
        "limit",
        3,
        "count",
        None,
        "hard",
    ),
    (
        "partner_apps",
        "Partner apps",
        "تطبيقات الشركاء",
        "apps",
        "limit",
        U,
        "count",
        None,
        "hard",
    ),
    (
        "api_access",
        "API & webhooks",
        "الواجهة البرمجية",
        "developers",
        "boolean",
        False,
        None,
        None,
        "hard",
    ),
    (
        "custom_domain",
        "Custom domain",
        "نطاق مخصص",
        "online_store",
        "boolean",
        False,
        None,
        None,
        "hard",
    ),
    (
        "discount_codes",
        "Discount codes",
        "أكواد الخصم",
        "marketing",
        "boolean",
        False,
        None,
        None,
        "hard",
    ),
    (
        "multi_warehouse",
        "Multiple locations",
        "فروع متعددة",
        "logistics",
        "boolean",
        False,
        None,
        None,
        "hard",
    ),
    (
        "product_subscriptions",
        "Product subscriptions",
        "اشتراكات المنتجات",
        "catalog",
        "boolean",
        False,
        None,
        None,
        "hard",
    ),
]

FIELD_TO_FEATURE = {
    "max_products": "products",
    "max_orders_per_month": "orders_per_month",
    "max_stores": "stores",
    "max_staff_members": "staff_accounts",
    "api_access_enabled": "api_access",
    "custom_domain_enabled": "custom_domain",
    "discount_codes_enabled": "discount_codes",
}
# plan: products, orders/mo, stores, staff, api, custom_domain, discount_codes
PLANS = {
    "demo": (10, 50, 1, 1, False, False, False),
    "trial": (-1, 500, 1, 3, False, True, True),
    "starter": (100, -1, 1, 3, False, True, True),
    "pro": (-1, -1, 3, 10, True, True, True),
    "developer": (-1, -1, 0, 3, True, False, True),
    "enterprise": (-1, -1, -1, -1, True, True, True),
    "payg": (100, -1, 1, 3, False, True, True),
    "free": (50, 100, 1, 1, False, False, False),
}
PLANS["beta"] = PLANS["trial"]
#: capability_service min_plan="pro"
PRO_AND_UP = {"pro", "developer", "enterprise"}
#: Decisions that win over both the snapshot and admin edits.
DECISIONS = {("starter", "products"): U}


def seed_rows(overrides: dict) -> list[tuple[str, str, object]]:
    """Plan grants: the snapshot, then prod's admin edits, then decisions."""
    rows = []
    for plan, values in PLANS.items():
        fields = dict(zip(FIELD_TO_FEATURE, values, strict=True))
        fields.update(
            (k, v)
            for k, v in (overrides.get(plan) or {}).items()
            if k in FIELD_TO_FEATURE
        )
        for field, feature in FIELD_TO_FEATURE.items():
            value = U if fields[field] == -1 else fields[field]
            rows.append((plan, feature, DECISIONS.get((plan, feature), value)))
        rows.append((plan, "partner_apps", U))
        for feature in ("multi_warehouse", "product_subscriptions"):
            rows.append((plan, feature, plan in PRO_AND_UP))
    return rows


def upgrade() -> None:
    # asyncpg runs one statement per execute, and CI's migration safety
    # check only reads literal SQL.
    op.execute(
        """
CREATE TABLE IF NOT EXISTS public.features (
    key             varchar(64)  PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    name            varchar(120) NOT NULL,
    name_ar         varchar(120) NOT NULL,
    description     text,
    category        varchar(40),
    kind            varchar(10)  NOT NULL CHECK (kind IN ('boolean', 'limit')),
    default_value   jsonb        NOT NULL,
    usage           varchar(10)  CHECK (usage IN ('count', 'counter')),
    period          varchar(10)  CHECK (period IN ('day', 'month')),
    enforcement     varchar(10)  NOT NULL DEFAULT 'hard'
                                 CHECK (enforcement IN ('hard', 'soft')),
    unit            varchar(20),
    is_enabled      boolean      NOT NULL DEFAULT true,
    disabled_reason text,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_features_default_value CHECK (
        CASE jsonb_typeof(default_value)
            WHEN 'boolean' THEN true
            WHEN 'number'  THEN default_value::numeric >= 0
                            AND default_value::numeric = trunc(default_value::numeric)
            WHEN 'string'  THEN default_value = '"unlimited"'::jsonb
            ELSE false
        END),
    CONSTRAINT ck_features_usage_only_on_limits
        CHECK (kind = 'limit' OR (usage IS NULL AND period IS NULL)),
    CONSTRAINT ck_features_period_needs_usage
        CHECK (period IS NULL OR usage IS NOT NULL)
)
"""
    )
    op.execute(
        """
CREATE TABLE IF NOT EXISTS public.plan_entitlements (
    plan_key    varchar(64) NOT NULL,
    feature_key varchar(64) NOT NULL
                REFERENCES public.features (key) ON DELETE CASCADE,
    value       jsonb       NOT NULL,
    updated_by  uuid        REFERENCES public.users (id) ON DELETE SET NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (plan_key, feature_key),
    CONSTRAINT ck_plan_entitlements_value CHECK (
        CASE jsonb_typeof(value)
            WHEN 'boolean' THEN true
            WHEN 'number'  THEN value::numeric >= 0
                            AND value::numeric = trunc(value::numeric)
            WHEN 'string'  THEN value = '"unlimited"'::jsonb
            ELSE false
        END)
)
"""
    )
    op.execute(
        """
CREATE TABLE IF NOT EXISTS public.entitlement_overrides (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL
                REFERENCES public.tenants (id) ON DELETE CASCADE,
    feature_key varchar(64) NOT NULL
                REFERENCES public.features (key) ON DELETE CASCADE,
    value       jsonb       NOT NULL,
    starts_at   timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz,
    source      varchar(20) NOT NULL CHECK (source IN
                ('support', 'sales', 'promotion', 'beta', 'contract',
                 'testing', 'migration')),
    reason      text        NOT NULL CHECK (length(btrim(reason)) >= 3),
    created_by  uuid        REFERENCES public.users (id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz,
    revoked_by  uuid        REFERENCES public.users (id) ON DELETE SET NULL,
    CONSTRAINT ck_entitlement_overrides_window
        CHECK (expires_at IS NULL OR expires_at > starts_at),
    CONSTRAINT ck_entitlement_overrides_value CHECK (
        CASE jsonb_typeof(value)
            WHEN 'boolean' THEN true
            WHEN 'number'  THEN value::numeric >= 0
                            AND value::numeric = trunc(value::numeric)
            WHEN 'string'  THEN value = '"unlimited"'::jsonb
            ELSE false
        END)
)
"""
    )
    op.execute(
        """
CREATE UNIQUE INDEX IF NOT EXISTS uq_entitlement_overrides_live
    ON public.entitlement_overrides (tenant_id, feature_key)
    WHERE revoked_at IS NULL
"""
    )
    op.execute(
        """
CREATE TABLE IF NOT EXISTS public.feature_flags (
    key             varchar(64) PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    description     text        NOT NULL,
    owner           varchar(120),
    feature_key     varchar(64)
                    REFERENCES public.features (key) ON DELETE SET NULL,
    enabled         boolean     NOT NULL DEFAULT false,
    rollout_percent smallint    NOT NULL DEFAULT 0
                    CHECK (rollout_percent BETWEEN 0 AND 100),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
)
"""
    )
    op.execute(
        """
CREATE TABLE IF NOT EXISTS public.feature_flag_targets (
    flag_key   varchar(64) NOT NULL
               REFERENCES public.feature_flags (key) ON DELETE CASCADE,
    tenant_id  uuid        NOT NULL
               REFERENCES public.tenants (id) ON DELETE CASCADE,
    enabled    boolean     NOT NULL DEFAULT true,
    expires_at timestamptz,
    reason     text,
    created_by uuid        REFERENCES public.users (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (flag_key, tenant_id)
)
"""
    )
    op.execute(
        """
CREATE INDEX IF NOT EXISTS ix_feature_flag_targets_tenant
    ON public.feature_flag_targets (tenant_id)
"""
    )
    op.execute(
        """
CREATE TABLE IF NOT EXISTS public.usage_counters (
    tenant_id    uuid        NOT NULL
                 REFERENCES public.tenants (id) ON DELETE CASCADE,
    feature_key  varchar(64) NOT NULL
                 REFERENCES public.features (key) ON DELETE CASCADE,
    period_start timestamptz NOT NULL,
    used         bigint      NOT NULL DEFAULT 0 CHECK (used >= 0),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, feature_key, period_start)
)
"""
    )
    op.execute(
        """
ALTER TABLE public.tenants
    ADD COLUMN IF NOT EXISTS entitlements_version integer NOT NULL DEFAULT 1
"""
    )
    op.execute(
        """
-- Supabase serves `public` over its Data API. These rows decide what a
-- merchant may use, so no role but the API's own may touch them.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON public.features, public.plan_entitlements,
            public.entitlement_overrides, public.feature_flags,
            public.feature_flag_targets, public.usage_counters
            FROM anon, authenticated;
    END IF;
END $$
"""
    )
    conn = op.get_bind()
    columns = (
        "key",
        "name",
        "name_ar",
        "category",
        "kind",
        "default",
        "usage",
        "period",
        "enforcement",
    )
    conn.execute(
        sa.text(
            "INSERT INTO public.features (key, name, name_ar, category, kind,"
            " default_value, usage, period, enforcement) VALUES (:key, :name,"
            " :name_ar, :category, :kind, CAST(:default AS jsonb), :usage,"
            " :period, :enforcement) ON CONFLICT (key) DO NOTHING"
        ),
        [
            dict(zip(columns, f, strict=True)) | {"default": json.dumps(f[5])}
            for f in FEATURES
        ],
    )
    overrides = (
        conn.execute(
            sa.text(
                "SELECT value FROM public.platform_config WHERE key = 'plan_limits'"
            )
        ).scalar()
        or {}
    )
    conn.execute(
        sa.text(
            "INSERT INTO public.plan_entitlements (plan_key, feature_key, value)"
            " VALUES (:p, :f, CAST(:v AS jsonb)) ON CONFLICT DO NOTHING"
        ),
        [{"p": p, "f": f, "v": json.dumps(v)} for p, f, v in seed_rows(overrides)],
    )
    # Per-tenant API grants move out of the flags JSON into audited overrides.
    conn.execute(
        sa.text(
            "INSERT INTO public.entitlement_overrides"
            " (tenant_id, feature_key, value, source, reason)"
            " SELECT id, 'api_access', 'true', 'migration',"
            "        'moved from tenants.feature_flags.api_access'"
            " FROM public.tenants"
            " WHERE feature_flags -> 'api_access' = 'true'::jsonb"
            " ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS public.usage_counters, public.feature_flag_targets,"
        " public.feature_flags, public.entitlement_overrides,"
        " public.plan_entitlements, public.features"
    )
    op.execute("ALTER TABLE public.tenants DROP COLUMN IF EXISTS entitlements_version")

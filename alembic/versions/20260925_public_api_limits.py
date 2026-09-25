"""Public API limits as entitlements, webhooks as their own feature, usage table.

Revision ID: public_api_limits_20260925
Revises: merge_partner_platform_0925
Create Date: 2026-09-25

* Four limit features drive the API key rate limiter and quota
  (``src/application/services/api_limits.py``). Their catalog defaults are
  the Pro numbers, so a merchant granted ``api_access`` by override on a
  lower plan gets working limits instead of zero. Only enterprise has plan
  rows above the default; admins tune anyone else with overrides.
* ``webhooks_access`` splits webhooks off ``api_access``. Every plan and
  every live grant starts exactly where ``api_access`` is, so nobody's
  webhooks change today.
* ``api_usage_daily`` holds the flushed per-day aggregates.

New catalog keys are missing from every cached snapshot, so every tenant's
entitlements_version moves on.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "public_api_limits_20260925"
down_revision: str | None = "merge_partner_platform_0925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Catalog rows, for tests that seed the catalog without running SQL.
#: key, name, name_ar, category, kind, default, usage, period, enforcement
FEATURES = (
    (
        "api_requests_per_minute",
        "API requests per minute",
        "طلبات API في الدقيقة",
        "developers",
        "limit",
        60,
        None,
        None,
        "hard",
    ),
    (
        "api_requests_per_second",
        "API burst (requests per second)",
        "طلبات API في الثانية",
        "developers",
        "limit",
        10,
        None,
        None,
        "hard",
    ),
    (
        "api_monthly_quota",
        "API requests per month",
        "طلبات API في الشهر",
        "developers",
        "limit",
        200000,
        None,
        None,
        "hard",
    ),
    (
        "api_key_limit",
        "API keys",
        "مفاتيح API",
        "developers",
        "limit",
        5,
        None,
        None,
        "hard",
    ),
    (
        "webhooks_access",
        "Webhooks",
        "الويب هوك",
        "developers",
        "boolean",
        False,
        None,
        None,
        "hard",
    ),
)
#: plan_key -> {feature: value} above the catalog default.
PLAN_VALUES = {
    "enterprise": {
        "api_requests_per_minute": 300,
        "api_requests_per_second": 30,
        "api_monthly_quota": 2000000,
        "api_key_limit": 25,
    },
}


def upgrade() -> None:
    op.execute(
        "INSERT INTO public.features (key, name, name_ar, category, kind,"
        " default_value, usage, period, enforcement) VALUES"
        " ('api_requests_per_minute', 'API requests per minute',"
        "  'طلبات API في الدقيقة', 'developers', 'limit', '60', NULL, NULL, 'hard'),"
        " ('api_requests_per_second', 'API burst (requests per second)',"
        "  'طلبات API في الثانية', 'developers', 'limit', '10', NULL, NULL, 'hard'),"
        " ('api_monthly_quota', 'API requests per month',"
        "  'طلبات API في الشهر', 'developers', 'limit', '200000', NULL, NULL, 'hard'),"
        " ('api_key_limit', 'API keys', 'مفاتيح API', 'developers', 'limit',"
        "  '5', NULL, NULL, 'hard'),"
        " ('webhooks_access', 'Webhooks', 'الويب هوك', 'developers', 'boolean',"
        "  'false', NULL, NULL, 'hard')"
        " ON CONFLICT (key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO public.plan_entitlements (plan_key, feature_key, value) VALUES"
        " ('enterprise', 'api_requests_per_minute', '300'),"
        " ('enterprise', 'api_requests_per_second', '30'),"
        " ('enterprise', 'api_monthly_quota', '2000000'),"
        " ('enterprise', 'api_key_limit', '25')"
        " ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO public.plan_entitlements (plan_key, feature_key, value)"
        " SELECT plan_key, 'webhooks_access', value FROM public.plan_entitlements"
        " WHERE feature_key = 'api_access'"
        " ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO public.entitlement_overrides"
        " (tenant_id, feature_key, value, starts_at, expires_at, source, reason)"
        " SELECT tenant_id, 'webhooks_access', value, starts_at, expires_at,"
        "        'migration', 'copied from the api_access grant when webhooks got their own feature'"
        " FROM public.entitlement_overrides"
        " WHERE feature_key = 'api_access' AND revoked_at IS NULL"
        "   AND (expires_at IS NULL OR expires_at > now())"
        " ON CONFLICT DO NOTHING"
    )
    op.execute(
        """
CREATE TABLE IF NOT EXISTS public.api_usage_daily (
    tenant_id       uuid         NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    day             date         NOT NULL,
    token_id        uuid         NOT NULL,
    method          varchar(8)   NOT NULL,
    route           varchar(200) NOT NULL,
    requests        integer      NOT NULL DEFAULT 0,
    errors_4xx      integer      NOT NULL DEFAULT 0,
    errors_5xx      integer      NOT NULL DEFAULT 0,
    throttled       integer      NOT NULL DEFAULT 0,
    latency_ms_sum  bigint       NOT NULL DEFAULT 0,
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, day, token_id, method, route)
)
"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_api_usage_daily_day ON public.api_usage_daily (day)"
    )
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON public.api_usage_daily FROM anon, authenticated;
    END IF;
END $$
"""
    )
    op.execute(
        "UPDATE public.tenants SET entitlements_version = entitlements_version + 1"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.api_usage_daily")
    op.execute(
        "DELETE FROM public.features WHERE key IN ('api_requests_per_minute',"
        " 'api_requests_per_second', 'api_monthly_quota', 'api_key_limit',"
        " 'webhooks_access')"
    )

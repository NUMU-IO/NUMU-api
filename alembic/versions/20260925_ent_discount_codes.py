"""Grandfather discount codes before creating one needs the plan (D4).

Revision ID: ent_discount_codes_20260925
Revises: ent_custom_domain_20260925
Create Date: 2026-09-24

Creating a coupon never checked the plan. Tenants that already have
coupons on a plan without discount codes (demo and free in the seed) keep
the feature through a `migration` override, and their entitlements_version
moves on. Shoppers redeeming an existing code never hit the check either
way.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ent_discount_codes_20260925"
down_revision: str | None = "ent_custom_domain_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
WITH granted AS (
    INSERT INTO public.entitlement_overrides
        (tenant_id, feature_key, value, source, reason)
    SELECT DISTINCT t.id, 'discount_codes', 'true'::jsonb, 'migration',
           'grandfathered 2026-09: had discount codes'
    FROM public.tenants t
    JOIN public.stores s ON s.tenant_id = t.id
    JOIN public.coupons c ON c.store_id = s.id
    WHERE NOT EXISTS (
        SELECT 1 FROM public.plan_entitlements pe
        WHERE pe.plan_key = t.plan AND pe.feature_key = 'discount_codes'
          AND pe.value = 'true'::jsonb)
    ON CONFLICT DO NOTHING
    RETURNING tenant_id
)
UPDATE public.tenants SET entitlements_version = entitlements_version + 1
WHERE id IN (SELECT tenant_id FROM granted)
"""
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM public.entitlement_overrides"
        " WHERE feature_key = 'discount_codes' AND source = 'migration'"
        " AND reason = 'grandfathered 2026-09: had discount codes'"
    )

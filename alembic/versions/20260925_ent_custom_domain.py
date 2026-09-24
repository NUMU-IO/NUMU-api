"""Grandfather custom domains before connecting one needs the plan (D4).

Revision ID: ent_custom_domain_20260925
Revises: ent_staff_20260925
Create Date: 2026-09-24

Connecting a custom domain never checked the plan. Tenants whose store
already has one and whose plan does not include custom domains (demo, free
and developer in the seed) keep them through a `migration` override, and
their entitlements_version moves on. Serving an existing domain never
checks the plan either way.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ent_custom_domain_20260925"
down_revision: str | None = "ent_staff_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
WITH granted AS (
    INSERT INTO public.entitlement_overrides
        (tenant_id, feature_key, value, source, reason)
    SELECT DISTINCT t.id, 'custom_domain', 'true'::jsonb, 'migration',
           'grandfathered 2026-09: had a custom domain'
    FROM public.tenants t
    JOIN public.stores s ON s.tenant_id = t.id
    WHERE s.custom_domain IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM public.plan_entitlements pe
          WHERE pe.plan_key = t.plan AND pe.feature_key = 'custom_domain'
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
        " WHERE feature_key = 'custom_domain' AND source = 'migration'"
        " AND reason = 'grandfathered 2026-09: had a custom domain'"
    )

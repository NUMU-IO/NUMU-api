"""Grandfather multi-warehouse before a second location needs the plan (D4).

Revision ID: ent_multi_warehouse_20260925
Revises: ent_discount_codes_20260925
Create Date: 2026-09-24

A store could create any number of locations whatever its plan. Tenants
that already have more than one (active or not, so they can switch one
back on) on a plan without multi_warehouse keep it through a `migration`
override, and their entitlements_version moves on.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ent_multi_warehouse_20260925"
down_revision: str | None = "ent_discount_codes_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
WITH granted AS (
    INSERT INTO public.entitlement_overrides
        (tenant_id, feature_key, value, source, reason)
    SELECT t.id, 'multi_warehouse', 'true'::jsonb, 'migration',
           'grandfathered 2026-09: had more than one location'
    FROM public.tenants t
    JOIN public.stores s ON s.tenant_id = t.id
    JOIN public.locations l ON l.store_id = s.id
    WHERE NOT EXISTS (
        SELECT 1 FROM public.plan_entitlements pe
        WHERE pe.plan_key = t.plan AND pe.feature_key = 'multi_warehouse'
          AND pe.value = 'true'::jsonb)
    GROUP BY t.id
    HAVING count(*) > 1
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
        " WHERE feature_key = 'multi_warehouse' AND source = 'migration'"
        " AND reason = 'grandfathered 2026-09: had more than one location'"
    )

"""Keep pixelprint and vionne out of the new enforcement for now.

Revision ID: ent_exempt_stores_20260925
Revises: ent_abandoned_cart_20260925
Create Date: 2026-09-24

The owner asked that these two stores see no change from decisions D4 and
D5 until they are reviewed. Each gets a `migration` override that grants
what the new checks take away: unlimited staff, custom domain, discount
codes, multi-warehouse and abandoned-cart recovery. The overrides are
ordinary rows, so an admin can revoke them one by one from the store's
Entitlements panel. Tenants are matched by subdomain; a subdomain that does
not exist matches nothing.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ent_exempt_stores_20260925"
down_revision: str | None = "ent_abandoned_cart_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
WITH granted AS (
    INSERT INTO public.entitlement_overrides
        (tenant_id, feature_key, value, source, reason)
    SELECT t.id, f.feature_key, f.value, 'migration',
           'exempt from D4/D5 enforcement until reviewed (owner, 2026-09-24)'
    FROM public.tenants t
    CROSS JOIN (VALUES
        ('staff_accounts', '"unlimited"'::jsonb),
        ('custom_domain', 'true'::jsonb),
        ('discount_codes', 'true'::jsonb),
        ('multi_warehouse', 'true'::jsonb),
        ('abandoned_cart', 'true'::jsonb)
    ) AS f (feature_key, value)
    WHERE t.subdomain IN ('pixelprint', 'vionne')
    ON CONFLICT DO NOTHING
    RETURNING tenant_id
)
UPDATE public.tenants SET entitlements_version = entitlements_version + 1
WHERE id IN (SELECT tenant_id FROM granted)
"""
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM public.entitlement_overrides WHERE source = 'migration'"
        " AND reason = 'exempt from D4/D5 enforcement until reviewed"
        " (owner, 2026-09-24)'"
    )

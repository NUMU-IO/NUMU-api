"""Grandfather staff seats before the staff limit is enforced (decision D4).

Revision ID: ent_staff_20260925
Revises: entitlement_indexes_20260925
Create Date: 2026-09-24

The staff limit was defined but never enforced, so some teams are already
bigger than their plan allows. Those tenants keep what they had, unlimited
staff, through a `migration` override; every other tenant is held to its
plan from this deploy on. A seat is a staff member who was not removed plus
an invitation still waiting for an answer, as EntitlementService counts it.
The affected tenants' entitlements_version moves on so no cached snapshot
outlives the grant.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ent_staff_20260925"
down_revision: str | None = "entitlement_indexes_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
WITH seats AS (
    SELECT t.id AS tenant_id, t.plan,
           (SELECT count(*) FROM public.tenant_memberships m
             WHERE m.tenant_id = t.id AND NOT m.is_owner
               AND m.deleted_at IS NULL AND m.status <> 'REVOKED')
         + (SELECT count(*) FROM public.staff_invitations i
             WHERE i.tenant_id = t.id AND i.accepted_at IS NULL
               AND i.revoked_at IS NULL AND i.expires_at > now()) AS used
    FROM public.tenants t
), over_limit AS (
    SELECT s.tenant_id
    FROM seats s
    JOIN public.plan_entitlements pe
      ON pe.plan_key = s.plan AND pe.feature_key = 'staff_accounts'
    WHERE jsonb_typeof(pe.value) = 'number'
      AND s.used > (pe.value #>> '{}')::int
), granted AS (
    INSERT INTO public.entitlement_overrides
        (tenant_id, feature_key, value, source, reason)
    SELECT tenant_id, 'staff_accounts', '"unlimited"'::jsonb, 'migration',
           'grandfathered 2026-09: had more staff than the plan allows'
    FROM over_limit
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
        " WHERE feature_key = 'staff_accounts' AND source = 'migration'"
        " AND reason = 'grandfathered 2026-09: had more staff than the plan allows'"
    )

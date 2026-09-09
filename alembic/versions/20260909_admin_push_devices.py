"""Let platform staff register a device for push.

``device_registrations`` was built for merchants, so ``tenant_id`` is NOT NULL
and the RLS policy confines every row to one tenant. Platform staff have no
tenant at all — an admin belongs to the platform, not to a store — so the admin
backoffice could not register a device without inventing a sentinel tenant to
hang the rows off.

A sentinel would be worse than it looks: it puts staff devices inside a real
merchant's isolation boundary, and every count, export and cascade that walks
tenants would then have to remember to skip it. So ``tenant_id`` becomes
nullable and NULL means exactly what it says — this device belongs to the
platform.

The policy is widened symmetrically: a platform row is visible only when NO
tenant context is set, which is the admin API and the Celery fan-out. A
merchant session, which always sets ``app.current_tenant``, still cannot see
them, and a platform session still cannot see a merchant's.

Revision ID: admin_push_devices_20260909
Revises: a2801f7661b1
Create Date: 2026-09-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "admin_push_devices_20260909"
down_revision: str | Sequence[str] | None = "a2801f7661b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.device_registrations ALTER COLUMN tenant_id DROP NOT NULL"
    )

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_device_registrations "
        "ON public.device_registrations"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_device_registrations
        ON public.device_registrations
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
            OR (
                tenant_id IS NULL
                AND NULLIF(current_setting('app.current_tenant', true), '') IS NULL
            )
        )
        """
    )

    # The fan-out to staff devices reads exactly this set, and the composite
    # index on (tenant_id, user_id, revoked_at) cannot serve it: a NULL
    # leading column is not a selective prefix.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_device_registrations_platform_active
        ON public.device_registrations (revoked_at)
        WHERE tenant_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_device_registrations_platform_active")

    # Staff rows have no tenant to fall back to, so restoring NOT NULL means
    # dropping them. They are re-created the next time an admin opens the
    # backoffice and re-subscribes.
    op.execute("DELETE FROM public.device_registrations WHERE tenant_id IS NULL")
    op.execute(
        "ALTER TABLE public.device_registrations ALTER COLUMN tenant_id SET NOT NULL"
    )

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_device_registrations "
        "ON public.device_registrations"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_device_registrations
        ON public.device_registrations
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )

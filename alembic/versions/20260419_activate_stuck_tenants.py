"""activate tenants stranded by create_store bug

Prior to this release, CreateStoreUseCase created tenants with
is_active = is_beta_invite, leaving every non-beta-invite tenant
inactive. TenantMiddleware then 404'd every request those owners
made to their own dashboards ("Store 'id=...' not found or inactive").

This migration repairs the stranded rows. We scope the update tightly:
only flip tenants whose associated store is still in PENDING_APPROVAL —
that's the exact fingerprint of the bug, and it avoids touching
tenants that were deliberately suspended.

Revision ID: activate_stuck_tenants_20260419
Revises: pr_reviews_20260419
Create Date: 2026-04-19 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "activate_stuck_tenants_20260419"
down_revision: str | None = "pr_reviews_20260419"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # storestatus is a Postgres enum using the enum NAME (uppercase), not the
    # StrEnum value. Cast explicitly so this isn't fragile to session search_path.
    op.execute(
        """
        UPDATE tenants t
           SET is_active = TRUE
          FROM stores s
         WHERE s.tenant_id = t.id
           AND t.is_active = FALSE
           AND s.status = 'PENDING_APPROVAL'::storestatus
        """
    )
    op.execute(
        """
        UPDATE stores
           SET status = 'ACTIVE'::storestatus
         WHERE status = 'PENDING_APPROVAL'::storestatus
        """
    )


def downgrade() -> None:
    # Intentional no-op: we can't reliably distinguish stores that were
    # stuck by the bug from stores that became active through normal
    # approval flow, and re-deactivating tenants would lock owners out
    # again.
    pass

"""broaden activate_stuck_tenants to cover already-approved stores

The earlier repair (activate_stuck_tenants_20260419) only flipped tenants
whose store was still in PENDING_APPROVAL. But some stores were manually
promoted to ACTIVE without ever flipping tenants.is_active — those
owners still hit "Store ... not found or inactive" on every dashboard
request.

The real invariant the app relies on: any tenant whose lifecycle_state
is ACTIVE must also have is_active=TRUE. This migration enforces that
invariant as a one-shot repair.

Revision ID: activate_tenants_broad_20260419
Revises: 297fcd759025
Create Date: 2026-04-19 19:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "activate_tenants_broad_20260419"
down_revision: str | None = "297fcd759025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tenants
           SET is_active = TRUE
         WHERE is_active = FALSE
           AND lifecycle_state = 'active'
        """
    )


def downgrade() -> None:
    # No-op: we cannot distinguish tenants we repaired from those that
    # were always correctly active.
    pass

"""Partner analytics: uninstall reasons and install date on uninstall events.

Revision ID: partner_analytics_20260924
Revises: merge_heads_20260925
Create Date: 2026-09-24

Additive, three nullable columns on ``app_uninstall_events``:
- ``installed_at``: when the removed installation was created, so the
  partner analytics can rebuild installed stores over time;
- ``reason`` / ``reason_text``: the optional answer the merchant gives in the
  uninstall dialog.

Idempotent (IF NOT EXISTS). Downgrade drops the columns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "partner_analytics_20260924"
down_revision: str | Sequence[str] | None = "merge_heads_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.app_uninstall_events
            ADD COLUMN IF NOT EXISTS installed_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS reason VARCHAR(32),
            ADD COLUMN IF NOT EXISTS reason_text VARCHAR(500)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.app_uninstall_events
            DROP COLUMN IF EXISTS reason_text,
            DROP COLUMN IF EXISTS reason,
            DROP COLUMN IF EXISTS installed_at
        """
    )

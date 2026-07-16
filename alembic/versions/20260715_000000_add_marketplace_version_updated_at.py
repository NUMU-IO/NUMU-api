"""Add updated_at to marketplace_theme_versions.

The theme_marketplace_watchdog task sweeps versions stuck in ``building`` past a
timeout by filtering on ``updated_at < cutoff`` and stamps ``updated_at`` when it
flips a version to ``build_failed``. The column was missing from the model/table
(its three sibling ``marketplace_*`` tables already carry it), so the task raised
``AttributeError: ... has no attribute 'updated_at'`` on every beat tick. Added
NOT NULL with a server default so existing rows backfill to ``now()`` at
migration time.

Revision ID: mtv_updated_at_20260715
Revises: pat_scopes_20260713
Create Date: 2026-07-15
"""

import sqlalchemy as sa

from alembic import op

revision: str = "mtv_updated_at_20260715"
down_revision: str | None = "pat_scopes_20260713"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "marketplace_theme_versions",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("marketplace_theme_versions", "updated_at", schema="public")

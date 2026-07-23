"""Add a preview window to marketplace theme installations (ADR-6).

The developer self-install path installs a version that has NOT been reviewed
so a theme author can iterate on their own store. Left unbounded that means
unreviewed third-party JavaScript serving real shoppers indefinitely — the
bypass ADR-6 forbids.

NULL keeps today's behaviour for every existing row (ordinary installs of
published versions never expire), so this is additive and safe to apply ahead
of the code that sets it.

Revision ID: installation_preview_20260720
Revises: platform_capabilities_20260720
"""

import sqlalchemy as sa

from alembic import op

revision = "installation_preview_20260720"
down_revision = "platform_capabilities_20260720"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "marketplace_theme_installations",
        sa.Column("preview_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    # Resolution filters on this on every themed request, and expiry sweeps
    # scan it, so both want the partial index rather than a seq scan.
    op.create_index(
        "ix_mti_preview_expires_at",
        "marketplace_theme_installations",
        ["preview_expires_at"],
        schema="public",
        postgresql_where=sa.text("preview_expires_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mti_preview_expires_at",
        table_name="marketplace_theme_installations",
        schema="public",
    )
    op.drop_column(
        "marketplace_theme_installations", "preview_expires_at", schema="public"
    )

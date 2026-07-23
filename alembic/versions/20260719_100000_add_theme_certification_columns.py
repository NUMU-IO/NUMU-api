"""Add certification columns to marketplace_theme_versions.

Backs the server-side lint gate: until now the theme CLI's 12 lint rules ran
only if a developer chose to run them locally, and nothing on the path to
publication checked anything. The build worker now runs them and records the
outcome here so reviewers can see it and approval can refuse to publish a
theme that failed.

All three columns are nullable with no backfill: rows that predate the gate
legitimately have no result, and `certification_tier` reads NULL as "legacy"
at the call sites rather than claiming a tier that was never assessed.

Revision ID: theme_certification_20260719
Revises: backfill_variants_skus_20260718
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "theme_certification_20260719"
down_revision = "backfill_variants_skus_20260718"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "marketplace_theme_versions",
        sa.Column("lint_status", sa.String(length=20), nullable=True),
        schema="public",
    )
    op.add_column(
        "marketplace_theme_versions",
        sa.Column("lint_issues", postgresql.JSONB(), nullable=True),
        schema="public",
    )
    op.add_column(
        "marketplace_theme_versions",
        sa.Column("certification_tier", sa.String(length=20), nullable=True),
        schema="public",
    )
    # Reviewers filter the queue by "what still needs a human", which is
    # pending_review rows whose automated result is anything but passed.
    op.create_index(
        "ix_mtv_lint_status",
        "marketplace_theme_versions",
        ["lint_status"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mtv_lint_status", table_name="marketplace_theme_versions", schema="public"
    )
    op.drop_column("marketplace_theme_versions", "certification_tier", schema="public")
    op.drop_column("marketplace_theme_versions", "lint_issues", schema="public")
    op.drop_column("marketplace_theme_versions", "lint_status", schema="public")

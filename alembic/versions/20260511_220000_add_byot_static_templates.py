"""Add error_template_url + loading_template_url to marketplace_theme_versions.

Revision ID: byot_static_templates_20260511
Revises: courier_stats_20260511
Create Date: 2026-05-11

Phase 7.3 — static BYOT templates for chrome that renders outside the
React tree (the client-side error boundary and the streaming loading
skeleton). The build worker uploads the theme's
`templates/error.html` + `templates/loading.html` to R2 and stamps the
resulting URLs here so the marketplace install path can copy them to
`store.theme_settings.external_theme.{error,loading}_template_url`
just like `bundle_url` + `css_url`.

Nullable on both columns: themes that don't declare static templates
(or pre-Phase-7 themes that never built one) just leave them NULL and
the storefront falls back to its hardcoded chrome.

No backfill needed — pre-existing version rows have NULL on both
columns, which matches the storefront's "use platform fallback"
behavior.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "byot_static_templates_20260511"
down_revision: str | None = "courier_stats_20260511"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketplace_theme_versions",
        sa.Column("error_template_url", sa.String(1024), nullable=True),
        schema="public",
    )
    op.add_column(
        "marketplace_theme_versions",
        sa.Column("loading_template_url", sa.String(1024), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column(
        "marketplace_theme_versions",
        "loading_template_url",
        schema="public",
    )
    op.drop_column(
        "marketplace_theme_versions",
        "error_template_url",
        schema="public",
    )

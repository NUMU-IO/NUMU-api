"""Add preview_image_url override to theme_admin_config.

Revision ID: theme_preview_url_20260503
Revises: theme_admin_config_20260503
Create Date: 2026-05-03

When admins upload a preview screenshot for a theme via the admin Themes
page, the URL of the uploaded asset is stored here. ``NULL`` means "no
override" — the storefront API falls back to the convention
``{STOREFRONT_ASSETS_BASE_URL}/themes/{slug}/preview.png`` so existing
checked-in screenshots keep working.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "theme_preview_url_20260503"
down_revision: str | None = "theme_admin_config_20260503"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "theme_admin_config",
        sa.Column("preview_image_url", sa.String(1024), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("theme_admin_config", "preview_image_url", schema="public")

"""Add name column to store_themes (theme library labels).

Revision ID: store_theme_name_20260627
Revises: e08fd0e10438
Create Date: 2026-06-27

Shopify-style theme management. A store can install the same theme multiple
times (Live + drafts / duplicates), so the catalog ``themes.name`` is no
longer enough to tell two installations apart. ``store_themes.name`` is an
optional per-installation label the merchant sets via Rename / Duplicate
("Copy of Bazar", "Holiday draft", …). NULL means "fall back to the catalog
theme name" — every legacy row reads exactly as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "store_theme_name_20260627"
down_revision: str = "e08fd0e10438"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "store_themes",
        sa.Column("name", sa.String(length=120), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("store_themes", "name", schema="public")

"""Extend marketplace_themes with admin-editable metadata.

Revision ID: marketplace_metadata_20260527
Revises: platform_default_theme_20260527
Create Date: 2026-05-27

Adds five admin-curated columns:

  * ``author_name``   — display name on the catalog card (e.g. "NUMU
                        Themes" or a third-party developer's brand).
  * ``author_url``    — link out to the developer's site / portfolio.
  * ``screenshots``   — JSON array of ``{url, alt, viewport}`` records
                        used by the theme-detail page carousel.
  * ``highlights``    — JSON array of ``{title, body, video_url?}`` tiles
                        (Shopify-style "3 key highlights" feature).
  * ``feature_tags``  — JSON array of short strings rendered as chips on
                        the catalog card (``"sticky-header"``,
                        ``"mega-menu"``, etc.).

``demo_store_url`` already exists on this table — not re-added. Earlier
draft of file 04 §2.2 included it; current schema already has it from
the original marketplace creation.

All five new columns are nullable / default-empty, so the migration is
non-destructive and sawsaw + rabbit (which don't appear in
``marketplace_themes`` at all) cannot be touched by it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "marketplace_metadata_20260527"
down_revision: str = "platform_default_theme_20260527"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("marketplace_themes", schema="public") as bop:
        bop.add_column(sa.Column("author_name", sa.String(length=128), nullable=True))
        bop.add_column(sa.Column("author_url", sa.String(length=512), nullable=True))
        bop.add_column(
            sa.Column(
                "screenshots",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
                comment=(
                    "Array of {url:str, alt:str, viewport:'mobile'|'desktop'}; "
                    "rendered as carousel on the theme-detail page."
                ),
            )
        )
        bop.add_column(
            sa.Column(
                "highlights",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
                comment=(
                    "Array of {title:str, body:str, video_url?:str}; "
                    "Shopify-style 3-tile feature spotlight."
                ),
            )
        )
        bop.add_column(
            sa.Column(
                "feature_tags",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
                comment=(
                    "Array of short strings rendered as chips on the catalog card "
                    "('sticky-header', 'mega-menu', 'color-swatches', ...)."
                ),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("marketplace_themes", schema="public") as bop:
        bop.drop_column("feature_tags")
        bop.drop_column("highlights")
        bop.drop_column("screenshots")
        bop.drop_column("author_url")
        bop.drop_column("author_name")

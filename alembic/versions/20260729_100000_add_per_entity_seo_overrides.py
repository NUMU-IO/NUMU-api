"""Per-entity SEO overrides on products and categories.

Merchants had no way to keep ONE page out of the index, point a duplicate at
its original, or drop a page from sitemap.xml — the only indexing control was
the store-wide `settings.seo.robots_indexing_enabled` switch, which is
all-or-nothing.

  * robots_noindex   — emit `noindex, follow` for this entity's page
  * canonical_url    — absolute URL this page should credit as the original
  * sitemap_exclude  — leave this URL out of sitemap.xml

All three default to the current behaviour (indexable, self-canonical,
included), so this is additive and cannot change what any existing store
publishes until a merchant opts in.

Revision ID: seo_overrides_20260729
Revises: merge_seo_slug_20260728
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "seo_overrides_20260729"
down_revision: str | None = "merge_seo_slug_20260728"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("products", "categories")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "robots_noindex",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            schema="public",
        )
        op.add_column(
            table,
            sa.Column("canonical_url", sa.String(length=2048), nullable=True),
            schema="public",
        )
        op.add_column(
            table,
            sa.Column(
                "sitemap_exclude",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            schema="public",
        )


def downgrade() -> None:
    for table in _TABLES:
        for column in ("sitemap_exclude", "canonical_url", "robots_noindex"):
            op.drop_column(table, column, schema="public")

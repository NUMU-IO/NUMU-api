"""Add previous_slugs to products + categories (rename never 404s a URL).

Revision ID: previous_slugs_20260727
Revises: rls_all_tenant_20260722
Create Date: 2026-07-27

Renaming a product or a category rewrites its storefront URL, so every
indexed URL and inbound link pointing at the old one 404s and the ranking
it had accumulated is discarded. Articles already carry ``previous_handles``
for exactly this; these two columns are the catalogue's version of it — the
storefront resolves a retired slug back to the row and 301s to the canonical
URL.

Additive and backward-compatible: two JSONB columns defaulting to ``'[]'``,
so existing rows need no backfill and code that predates them is unaffected.
No GIN index — the containment lookup runs only when a slug MISSES, is
already narrowed by the existing store_id index, and the largest catalogue
on the platform is in the hundreds of rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "previous_slugs_20260727"
down_revision: str = "rls_all_tenant_20260722"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    for table in ("products", "categories"):
        op.add_column(
            table,
            sa.Column(
                "previous_slugs",
                JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            schema="public",
        )


def downgrade() -> None:
    for table in ("products", "categories"):
        op.drop_column(table, "previous_slugs", schema="public")

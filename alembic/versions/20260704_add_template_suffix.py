"""Add template_suffix to products, categories, and pages.

I1 of the template-overrides epic. Adds a nullable ``template_suffix``
string column to ``products``, ``categories``, and ``pages`` so a single
resource can render under an alternate Shopify-style template variant
(``<base>.<suffix>``, e.g. ``product.wholesale``). Null = the base
template. Additive with no backfill — safe on the hot ``products`` table.

No index — the column is only read alongside the rest of the resource row
when serving the detail page, which is already in the buffer cache from
the surrounding fetch.

Revision ID: add_template_suffix_20260704
Revises: tiktok_shop_20260701
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_template_suffix_20260704"
down_revision: str | None = "tiktok_shop_20260701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("template_suffix", sa.String(length=32), nullable=True),
        schema="public",
    )
    op.add_column(
        "categories",
        sa.Column("template_suffix", sa.String(length=32), nullable=True),
        schema="public",
    )
    op.add_column(
        "pages",
        sa.Column("template_suffix", sa.String(length=32), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("pages", "template_suffix", schema="public")
    op.drop_column("categories", "template_suffix", schema="public")
    op.drop_column("products", "template_suffix", schema="public")

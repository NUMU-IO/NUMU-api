"""Add meta_catalog_id to products for Meta Commerce Catalog alignment.

Revision ID: product_meta_catalog_20260516
Revises: is_internal_20260723
Create Date: 2026-05-16

Adds a nullable ``meta_catalog_id`` string column to ``products``. When
a merchant syncs their product catalog to Meta Commerce Manager (via
the upcoming catalog-sync pipeline, or manually pasting the Meta Catalog
product ID into the dashboard), the storefront uses this value as the
``content_ids`` field on ViewContent/AddToCart/Purchase Pixel events
so Meta's dynamic-product-ads engine can match the conversion to a
catalog row.

Null = the storefront falls back to our internal product UUID, which
won't link to a Meta Catalog product but still fires the event (just
without dynamic-ads matchability).

No index — this column is only read alongside the rest of the product
row when serving PDP/PLP pages. The product row is already in the
buffer cache from the surrounding fetch.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "product_meta_catalog_20260516"
down_revision: str | None = "is_internal_20260723"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("meta_catalog_id", sa.String(length=255), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("products", "meta_catalog_id", schema="public")

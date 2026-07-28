"""Add products.brand.

The storefront's Product JSON-LD reads `product.brand || product.vendor ||
storeName`, and neither top-level field was ever emitted — so every product on
the platform told Google it was the store's own house brand. The Meta feed read
`attributes.brand`, a different level, so a merchant who set it via the API got
it in the feed and NOT in the markup.

Additive and non-breaking: nothing reads a top-level brand today, and the feed
keeps `attributes.brand` as a fallback for merchants who already set it there.

Revision ID: product_brand_20260728
Revises: seo_backfill_20260728
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "product_brand_20260728"
down_revision: str | None = "seo_backfill_20260728"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("brand", sa.String(length=120), nullable=True),
        schema="public",
    )
    # Promote anything merchants already set through the API so the column is
    # correct from the first request rather than after their next save.
    op.execute(
        """
        UPDATE public.products
           SET brand = btrim(attributes ->> 'brand')
         WHERE brand IS NULL
           AND jsonb_typeof(attributes -> 'brand') = 'string'
           AND btrim(attributes ->> 'brand') <> ''
        """
    )


def downgrade() -> None:
    op.drop_column("products", "brand", schema="public")

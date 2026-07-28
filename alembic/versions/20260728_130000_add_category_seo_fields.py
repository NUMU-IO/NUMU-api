"""Add SEO fields to categories.

Collection pages are indexable, sitemapped and often the strongest
category-keyword page a store has, but the category model carried no
seo_title / seo_description / social image — so every collection page fell
back to the raw category name with no meta description of its own.

Purely additive: nothing reads these keys today, so it cannot regress. The
storefront's `localizedSeoText` already knows how to read them once they land.

Revision ID: category_seo_20260728
Revises: product_brand_20260728
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "category_seo_20260728"
down_revision: str | None = "product_brand_20260728"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = (
    ("seo_title", 70),
    ("seo_description", 160),
    ("social_image_url", 2048),
)


def upgrade() -> None:
    for name, length in _COLUMNS:
        op.add_column(
            "categories",
            sa.Column(name, sa.String(length=length), nullable=True),
            schema="public",
        )


def downgrade() -> None:
    for name, _ in _COLUMNS:
        op.drop_column("categories", name, schema="public")

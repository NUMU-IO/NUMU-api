"""Add product/category targeting fields to coupons table.

Revision ID: 9a8b7c6d5e4f
Revises: 8f7e4d3c2b1a
Create Date: 2026-02-20

Adds applicable_product_ids and applicable_category_ids ARRAY(UUID) columns
so merchants can restrict coupons to specific products or categories.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

# revision identifiers
revision = "9a8b7c6d5e4f"
down_revision = "8f7e4d3c2b1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "coupons",
        sa.Column("applicable_product_ids", ARRAY(UUID(as_uuid=True)), nullable=True),
        schema="public",
    )
    op.add_column(
        "coupons",
        sa.Column("applicable_category_ids", ARRAY(UUID(as_uuid=True)), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("coupons", "applicable_category_ids", schema="public")
    op.drop_column("coupons", "applicable_product_ids", schema="public")

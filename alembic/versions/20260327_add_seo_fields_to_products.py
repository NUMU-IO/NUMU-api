"""Add seo_title and seo_description fields to products table.

Revision ID: ee7788990033
Revises: ee7788990022
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "ee7788990033"
down_revision = "ee7788990022"

branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("seo_title", sa.String(60), nullable=True),
        schema="public",
    )
    op.add_column(
        "products",
        sa.Column("seo_description", sa.String(160), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("products", "seo_description", schema="public")
    op.drop_column("products", "seo_title", schema="public")

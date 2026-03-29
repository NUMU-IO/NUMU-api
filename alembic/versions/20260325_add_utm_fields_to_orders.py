"""Add UTM tracking fields to orders table.

Revision ID: aa1122334455
Revises: bb3344556677
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "aa1122334455"
down_revision = "bb3344556677"

branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("utm_source", sa.String(200), nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column("utm_medium", sa.String(200), nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column("utm_campaign", sa.String(200), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_public_orders_utm_source", "orders", ["utm_source"], schema="public"
    )
    op.create_index(
        "ix_public_orders_utm_campaign", "orders", ["utm_campaign"], schema="public"
    )


def downgrade() -> None:
    op.drop_index("ix_public_orders_utm_campaign", table_name="orders", schema="public")
    op.drop_index("ix_public_orders_utm_source", table_name="orders", schema="public")
    op.drop_column("orders", "utm_campaign", schema="public")
    op.drop_column("orders", "utm_medium", schema="public")
    op.drop_column("orders", "utm_source", schema="public")

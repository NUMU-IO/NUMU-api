"""add product_reviews table

Creates the public.product_reviews table that stores customer reviews of
products. Tenant-scoped via tenant_id, linked to store, product, and
optionally the customer who wrote it (nullable so guest reviews can be
imported without a customer record).

Revision ID: pr_reviews_20260419
Revises: 952b0522c1df
Create Date: 2026-04-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "pr_reviews_20260419"
down_revision: str | None = "952b0522c1df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewer_name", sa.String(length=120), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "is_approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "helpful_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating >= 1 AND rating <= 5", name="ck_product_reviews_rating_range"
        ),
        schema="public",
    )

    op.create_index(
        "ix_product_reviews_product_id",
        "product_reviews",
        ["product_id"],
        schema="public",
    )
    op.create_index(
        "ix_product_reviews_store_id",
        "product_reviews",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_product_reviews_product_approved",
        "product_reviews",
        ["product_id", "is_approved"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_reviews_product_approved",
        table_name="product_reviews",
        schema="public",
    )
    op.drop_index(
        "ix_product_reviews_store_id",
        table_name="product_reviews",
        schema="public",
    )
    op.drop_index(
        "ix_product_reviews_product_id",
        table_name="product_reviews",
        schema="public",
    )
    op.drop_table("product_reviews", schema="public")

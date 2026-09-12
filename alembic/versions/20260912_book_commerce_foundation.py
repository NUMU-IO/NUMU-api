"""Add variant fulfillment behavior and ordered product series.

Revision ID: book_commerce_20260912
Revises: lead_referral_used_20260909
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "book_commerce_20260912"
down_revision: str | None = "lead_referral_used_20260909"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_variants",
        sa.Column(
            "fulfillment_type", sa.String(16), nullable=False, server_default="physical"
        ),
        schema="public",
    )
    op.add_column(
        "product_variants",
        sa.Column(
            "requires_shipping", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        schema="public",
    )
    op.add_column(
        "product_variants",
        sa.Column(
            "track_inventory", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        schema="public",
    )
    op.create_check_constraint(
        "ck_variants_fulfillment_type",
        "product_variants",
        "fulfillment_type IN ('physical', 'digital', 'service')",
        schema="public",
    )

    op.create_table(
        "series",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cover_image_url", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["public.stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "slug", name="uq_series_store_slug"),
        schema="public",
    )
    op.create_index("ix_series_store", "series", ["store_id"], schema="public")
    op.create_index("ix_series_tenant_id", "series", ["tenant_id"], schema="public")

    op.create_table(
        "series_products",
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("volume_label", sa.String(32), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"], ["public.products.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["series_id"], ["public.series.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("series_id", "product_id"),
        sa.UniqueConstraint("series_id", "position", name="uq_series_position"),
        schema="public",
    )
    op.create_index(
        "ix_series_products_product", "series_products", ["product_id"], schema="public"
    )
    op.create_index(
        "ix_series_products_tenant_id",
        "series_products",
        ["tenant_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("series_products", schema="public")
    op.drop_table("series", schema="public")
    op.drop_constraint(
        "ck_variants_fulfillment_type",
        "product_variants",
        schema="public",
        type_="check",
    )
    op.drop_column("product_variants", "track_inventory", schema="public")
    op.drop_column("product_variants", "requires_shipping", schema="public")
    op.drop_column("product_variants", "fulfillment_type", schema="public")

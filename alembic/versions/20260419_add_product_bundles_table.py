"""Add product_bundles table for Frequently Bought Together feature.

Creates the public.product_bundles table that stores merchant-curated
product bundle associations. Each row links a primary product to a
bundled product with optional discount and display ordering.

Revision ID: fbt_bundles_20260419
Revises: pr_reviews_20260419
Create Date: 2026-04-19 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fbt_bundles_20260419"
down_revision: str = "pr_reviews_20260419"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_bundles",
        # ── PK & tenant ────────────────────────────────────────────
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ── Product references ─────────────────────────────────────
        sa.Column(
            "primary_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bundled_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ── Discount ───────────────────────────────────────────────
        sa.Column(
            "discount_type",
            sa.String(20),
            nullable=False,
            server_default="none",
            comment="percentage | fixed | none",
        ),
        sa.Column(
            "discount_value",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="Percentage (0-100) or fixed amount in cents",
        ),
        # ── Display ────────────────────────────────────────────────
        sa.Column(
            "position",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="Sort order in the bundle widget",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "section_title_en",
            sa.String(200),
            nullable=True,
            comment="Custom widget heading (English)",
        ),
        sa.Column(
            "section_title_ar",
            sa.String(200),
            nullable=True,
            comment="Custom widget heading (Arabic)",
        ),
        # ── Timestamps ─────────────────────────────────────────────
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # ── Constraints ────────────────────────────────────────────
        sa.UniqueConstraint(
            "store_id",
            "primary_product_id",
            "bundled_product_id",
            name="uq_bundle_pair_per_store",
        ),
        schema="public",
    )

    # ── Indexes ────────────────────────────────────────────────────
    op.create_index(
        "ix_product_bundles_tenant_id",
        "product_bundles",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_product_bundles_store_id",
        "product_bundles",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_product_bundles_primary_product_id",
        "product_bundles",
        ["primary_product_id"],
        schema="public",
    )
    op.create_index(
        "ix_product_bundles_bundled_product_id",
        "product_bundles",
        ["bundled_product_id"],
        schema="public",
    )
    # Composite index for the most common storefront query
    op.create_index(
        "ix_product_bundles_store_primary_active",
        "product_bundles",
        ["store_id", "primary_product_id", "is_active"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_bundles_store_primary_active",
        table_name="product_bundles",
        schema="public",
    )
    op.drop_index(
        "ix_product_bundles_bundled_product_id",
        table_name="product_bundles",
        schema="public",
    )
    op.drop_index(
        "ix_product_bundles_primary_product_id",
        table_name="product_bundles",
        schema="public",
    )
    op.drop_index(
        "ix_product_bundles_store_id",
        table_name="product_bundles",
        schema="public",
    )
    op.drop_index(
        "ix_product_bundles_tenant_id",
        table_name="product_bundles",
        schema="public",
    )
    op.drop_table("product_bundles", schema="public")

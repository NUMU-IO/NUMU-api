"""Add upsell_rules table for post-purchase upsell offers.

Revision ID: cc5566778899
Revises: aa1122334455
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = "cc5566778899"
down_revision = "aa1122334455"

branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upsell_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        # Trigger
        sa.Column("trigger_type", sa.String(20), nullable=False, server_default="any"),
        sa.Column(
            "trigger_product_ids",
            postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "trigger_category_ids",
            postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "trigger_min_cart_value", sa.Integer, nullable=False, server_default="0"
        ),
        # Offer
        sa.Column(
            "offer_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "discount_type", sa.String(20), nullable=False, server_default="percentage"
        ),
        sa.Column("discount_value", sa.Integer, nullable=False, server_default="0"),
        # Limits
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_uses", sa.Integer, nullable=True),
        sa.Column("uses_count", sa.Integer, nullable=False, server_default="0"),
        # Display
        sa.Column(
            "headline_ar",
            sa.String(200),
            nullable=False,
            server_default="عرض خاص لك! 🎁",
        ),
        sa.Column(
            "headline_en",
            sa.String(200),
            nullable=False,
            server_default="Special offer for you! 🎁",
        ),
        sa.Column("description_ar", sa.Text, nullable=True),
        sa.Column("description_en", sa.Text, nullable=True),
        # Timestamps
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
        schema="public",
    )

    # Indexes
    op.create_index(
        "ix_public_upsell_rules_store_id",
        "upsell_rules",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_public_upsell_rules_offer_product_id",
        "upsell_rules",
        ["offer_product_id"],
        schema="public",
    )
    op.create_index(
        "ix_public_upsell_rules_tenant_id",
        "upsell_rules",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_public_upsell_rules_store_active_priority",
        "upsell_rules",
        ["store_id", "is_active", "priority"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_public_upsell_rules_store_active_priority",
        table_name="upsell_rules",
        schema="public",
    )
    op.drop_index(
        "ix_public_upsell_rules_tenant_id",
        table_name="upsell_rules",
        schema="public",
    )
    op.drop_index(
        "ix_public_upsell_rules_offer_product_id",
        table_name="upsell_rules",
        schema="public",
    )
    op.drop_index(
        "ix_public_upsell_rules_store_id",
        table_name="upsell_rules",
        schema="public",
    )
    op.drop_table("upsell_rules", schema="public")

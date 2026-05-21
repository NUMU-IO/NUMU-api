"""Add saved_payment_methods table for one-click upsell charges.

Revision ID: dd6677889900
Revises: cc5566778899
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = "dd6677889900"
down_revision = "cc5566778899"

branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_payment_methods",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("gateway", sa.String(50), nullable=False),
        sa.Column("card_token", sa.String(500), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("card_brand", sa.String(50), nullable=True),
        sa.Column("last_four", sa.String(4), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
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


def downgrade() -> None:
    op.drop_table("saved_payment_methods", schema="public")

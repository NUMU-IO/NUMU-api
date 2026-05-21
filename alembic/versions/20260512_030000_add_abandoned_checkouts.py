"""Add abandoned_checkouts table.

Revision ID: abandoned_checkouts_20260512
Revises: draft_orderstatus_20260512
Create Date: 2026-05-12

Abandoned checkouts are persisted separately from Orders (the Shopify
model). The storefront writes / updates a row as the customer progresses
through checkout; on successful payment the row graduates to an Order
and `recovered_at` is set. A background job (out of scope here) flips
`abandoned_at` once a row sits inactive past the threshold.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "abandoned_checkouts_20260512"
down_revision: str | None = "draft_orderstatus_20260512"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "abandoned_checkouts",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("line_items", JSONB(), nullable=False, server_default="[]"),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("shipping_address", JSONB(), nullable=True),
        sa.Column("subtotal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipping_cost", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("coupon_code", sa.String(50), nullable=True),
        sa.Column("utm_source", sa.String(100), nullable=True),
        sa.Column("utm_medium", sa.String(100), nullable=True),
        sa.Column("utm_campaign", sa.String(100), nullable=True),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recovered_order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("extra_data", JSONB(), nullable=True, server_default="{}"),
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
        schema="public",
    )

    op.create_index(
        "idx_abandoned_checkouts_store_abandoned",
        "abandoned_checkouts",
        ["store_id", "abandoned_at"],
        schema="public",
    )
    op.create_index(
        "idx_abandoned_checkouts_store_last_activity",
        "abandoned_checkouts",
        ["store_id", "last_activity_at"],
        schema="public",
    )
    op.create_index(
        "idx_abandoned_checkouts_email",
        "abandoned_checkouts",
        ["email"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_abandoned_checkouts_email",
        table_name="abandoned_checkouts",
        schema="public",
    )
    op.drop_index(
        "idx_abandoned_checkouts_store_last_activity",
        table_name="abandoned_checkouts",
        schema="public",
    )
    op.drop_index(
        "idx_abandoned_checkouts_store_abandoned",
        table_name="abandoned_checkouts",
        schema="public",
    )
    op.drop_table("abandoned_checkouts", schema="public")

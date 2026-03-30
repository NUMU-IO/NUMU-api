"""Add shipments table for carrier delivery tracking.

Revision ID: bb3344556677
Revises: 4a8ec78130c6
Create Date: 2026-03-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "bb3344556677"
down_revision: str | None = "4a8ec78130c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Carrier info
        sa.Column("carrier", sa.String(50), nullable=False, server_default="bosta"),
        sa.Column("carrier_shipment_id", sa.String(255), nullable=True),
        sa.Column("tracking_number", sa.String(100), nullable=True),
        sa.Column("tracking_url", sa.String(500), nullable=True),
        sa.Column("awb_url", sa.String(500), nullable=True),
        # Status
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        # Type & linkage
        sa.Column(
            "shipment_type", sa.String(20), nullable=False, server_default="forward"
        ),
        sa.Column(
            "parent_shipment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.shipments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Shipping details
        sa.Column("shipping_method", sa.String(50), nullable=True),
        sa.Column("shipping_cost", sa.Integer(), nullable=False, server_default="0"),
        # COD
        sa.Column("cod_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cod_collected", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("cod_collected_at", sa.DateTime(timezone=True), nullable=True),
        # Delivery tracking
        sa.Column(
            "delivery_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("status_history", JSONB, nullable=False, server_default="[]"),
        sa.Column("extra_data", JSONB, nullable=True, server_default="{}"),
        # Timestamps
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
        "idx_shipments_tenant_id", "shipments", ["tenant_id"], schema="public"
    )
    op.create_index(
        "idx_shipments_store_id", "shipments", ["store_id"], schema="public"
    )
    op.create_index(
        "idx_shipments_order_id", "shipments", ["order_id"], schema="public"
    )
    op.create_index(
        "idx_shipments_tracking_number",
        "shipments",
        ["tracking_number"],
        schema="public",
    )
    op.create_index(
        "idx_shipments_carrier_shipment_id",
        "shipments",
        ["carrier_shipment_id"],
        schema="public",
    )
    op.create_index(
        "idx_shipments_store_id_status",
        "shipments",
        ["store_id", "status"],
        schema="public",
    )
    op.create_index(
        "idx_shipments_store_id_created_at",
        "shipments",
        ["store_id", "created_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("shipments", schema="public")

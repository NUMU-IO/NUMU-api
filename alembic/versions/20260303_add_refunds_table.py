"""Add refunds table for refund/return system.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-03-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create enum types (use PG_ENUM with create_type=False to prevent
    # duplicate creation when create_table fires _on_table_create)
    refundstatus = PG_ENUM(
        "REQUESTED",
        "APPROVED",
        "PROCESSING",
        "PROCESSED",
        "COMPLETED",
        "REJECTED",
        "FAILED",
        name="refundstatus",
        schema="public",
        create_type=False,
    )
    refundtype = PG_ENUM(
        "FULL",
        "PARTIAL",
        name="refundtype",
        schema="public",
        create_type=False,
    )
    refundreason = PG_ENUM(
        "DEFECTIVE",
        "WRONG_ITEM",
        "NOT_AS_DESCRIBED",
        "CUSTOMER_REQUEST",
        "DUPLICATE_ORDER",
        "OTHER",
        name="refundreason",
        schema="public",
        create_type=False,
    )

    refundstatus.create(op.get_bind(), checkfirst=True)
    refundtype.create(op.get_bind(), checkfirst=True)
    refundreason.create(op.get_bind(), checkfirst=True)

    # Create refunds table
    op.create_table(
        "refunds",
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
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("refund_number", sa.String(50), nullable=False),
        sa.Column("refund_type", refundtype, nullable=False),
        sa.Column(
            "status",
            refundstatus,
            nullable=False,
            server_default="REQUESTED",
        ),
        sa.Column("reason", refundreason, nullable=False),
        sa.Column("reason_note", sa.Text(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False, default=0),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("payment_provider", sa.String(50), nullable=True),
        sa.Column("payment_id", sa.String(255), nullable=True),
        sa.Column("provider_refund_id", sa.String(255), nullable=True),
        sa.Column(
            "requested_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "approved_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "rejected_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True, server_default="{}"),
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

    # Create indexes
    op.create_index(
        "idx_refunds_order_id",
        "refunds",
        ["order_id"],
        schema="public",
    )
    op.create_index(
        "idx_refunds_store_status",
        "refunds",
        ["store_id", "status"],
        schema="public",
    )
    op.create_index(
        "idx_refunds_store_created",
        "refunds",
        ["store_id", sa.text("created_at DESC")],
        schema="public",
    )
    op.create_index(
        "idx_refunds_refund_number",
        "refunds",
        ["refund_number"],
        schema="public",
    )


def downgrade() -> None:
    # Drop indexes first
    op.drop_index("idx_refunds_refund_number", table_name="refunds", schema="public")
    op.drop_index("idx_refunds_store_created", table_name="refunds", schema="public")
    op.drop_index("idx_refunds_store_status", table_name="refunds", schema="public")
    op.drop_index("idx_refunds_order_id", table_name="refunds", schema="public")

    # Drop table
    op.drop_table("refunds", schema="public")

    # Drop enum types
    sa.Enum(name="refundreason", schema="public").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="refundtype", schema="public").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="refundstatus", schema="public").drop(op.get_bind(), checkfirst=True)

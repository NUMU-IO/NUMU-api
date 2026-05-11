"""Add `source` discriminator column to orders / customers / shipments.

Backend-026 / spec 017 — schema-only platform-abstraction v1. Existing
rows take the server_default of 'shopify' (no separate UPDATE needed).
Future adapters (Salla, Zid, WooCommerce, TikTok Shops) write into the
same tables with their own source value.

Revision ID: platform_source_20260511
Revises: otp_codes_20260511
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "platform_source_20260511"
down_revision: str | None = "otp_codes_20260511"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ORDER_SOURCE_VALUES = (
    "shopify",
    "woocommerce",
    "salla",
    "zid",
    "numu_native",
    "tiktok_shop",
)


def upgrade() -> None:
    # Create the enum type once.
    op.execute(
        "CREATE TYPE ordersource AS ENUM ("
        + ", ".join(f"'{v}'" for v in ORDER_SOURCE_VALUES)
        + ")"
    )

    enum_col = postgresql.ENUM(
        *ORDER_SOURCE_VALUES,
        name="ordersource",
        create_type=False,
    )

    for table in ("orders", "customers", "shipments"):
        op.add_column(
            table,
            sa.Column(
                "source",
                enum_col,
                nullable=False,
                server_default=sa.text("'shopify'"),
            ),
            schema="public",
        )

    # Order is the most-queried by source (risk scoring + recovery filter
    # by source). Index it. Customer + Shipment can stay unindexed for v1
    # — they're rarely queried by source alone.
    op.create_index(
        "ix_orders_source",
        "orders",
        ["source"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("ix_orders_source", table_name="orders", schema="public")
    for table in ("shipments", "customers", "orders"):
        op.drop_column(table, "source", schema="public")
    op.execute("DROP TYPE IF EXISTS ordersource")

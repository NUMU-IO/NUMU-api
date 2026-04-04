"""add analytics_daily_rollups table

Revision ID: 4a7b2c8d9e0f
Revises: 239e629beae1
Create Date: 2026-04-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "4a7b2c8d9e0f"
down_revision: str | None = "239e629beae1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_daily_rollups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("rollup_date", sa.Date, nullable=False),
        # Revenue
        sa.Column("total_revenue_cents", sa.Integer, server_default="0"),
        sa.Column("total_orders", sa.Integer, server_default="0"),
        sa.Column("paid_orders", sa.Integer, server_default="0"),
        sa.Column("cancelled_orders", sa.Integer, server_default="0"),
        sa.Column("avg_order_value_cents", sa.Integer, server_default="0"),
        # Customers
        sa.Column("new_customers", sa.Integer, server_default="0"),
        sa.Column("returning_customers", sa.Integer, server_default="0"),
        # Traffic
        sa.Column("total_page_views", sa.Integer, server_default="0"),
        sa.Column("unique_visitors", sa.Integer, server_default="0"),
        # COD
        sa.Column("cod_orders", sa.Integer, server_default="0"),
        sa.Column("cod_delivered", sa.Integer, server_default="0"),
        sa.Column("cod_rejected", sa.Integer, server_default="0"),
        # Refunds
        sa.Column("refund_count", sa.Integer, server_default="0"),
        sa.Column("refund_amount_cents", sa.Integer, server_default="0"),
        # JSONB breakdowns
        sa.Column("top_products_json", JSONB, server_default="[]"),
        sa.Column("revenue_by_location_json", JSONB, server_default="[]"),
        sa.Column("traffic_sources_json", JSONB, server_default="[]"),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        schema="public",
    )

    # Unique composite index: one rollup per store per day
    op.create_index(
        "ix_rollup_store_date",
        "analytics_daily_rollups",
        ["store_id", "rollup_date"],
        unique=True,
        schema="public",
    )

    # RLS policy
    op.execute(
        """
        ALTER TABLE public.analytics_daily_rollups ENABLE ROW LEVEL SECURITY;
        """
    )
    op.execute(
        """
        CREATE POLICY analytics_rollup_tenant_isolation
        ON public.analytics_daily_rollups
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS analytics_rollup_tenant_isolation
        ON public.analytics_daily_rollups;
        """
    )
    op.drop_index(
        "ix_rollup_store_date",
        table_name="analytics_daily_rollups",
        schema="public",
    )
    op.drop_table("analytics_daily_rollups", schema="public")

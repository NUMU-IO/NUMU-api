"""Add courier_stats table (backend-023 / spec 013).

Per-store / per-carrier / per-period delivery rollup. Refreshed nightly
by the courier_stats Celery task; read by the Courier Intelligence
dashboard section.

Revision ID: courier_stats_20260511
Revises: flow_trigger_log_20260511
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "courier_stats_20260511"
down_revision: str | None = "flow_trigger_log_20260511"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "courier_stats",
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
            primary_key=True,
        ),
        sa.Column(
            "carrier",
            sa.String(50),
            primary_key=True,
        ),
        sa.Column(
            "period_start",
            sa.Date,
            primary_key=True,
        ),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column(
            "total_shipments",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "delivered_count",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "returned_count",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "failed_count",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "in_progress_count",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cod_collected_count",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cod_total_count",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("delivery_success_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("cod_collection_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("avg_delivery_hours", sa.Numeric(7, 2), nullable=True),
        sa.Column(
            "last_refreshed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
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
        "ix_courier_stats_store_period",
        "courier_stats",
        ["store_id", "period_start"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_courier_stats_store_period",
        table_name="courier_stats",
        schema="public",
    )
    op.drop_table("courier_stats", schema="public")

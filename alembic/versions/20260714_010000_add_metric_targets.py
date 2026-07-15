"""Add metric_targets — merchant goals with pace tracking.

One row per (store, metric, period). ``target_value`` is an integer in
the metric's smallest unit (cents for revenue/aov, count for orders,
basis points for conversion) so no floats touch money. Progress/pace are
computed at read time from the analytics rollups; the row stores only
the goal.

Revision ID: metric_targets_20260714
Revises: pat_scopes_20260713
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "metric_targets_20260714"
down_revision: str | None = "pat_scopes_20260713"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "metric_targets",
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
        # Plain strings validated at the API layer — deliberately NOT a
        # PG enum (the orderstatus lowercase-value saga).
        sa.Column("metric", sa.String(20), nullable=False),
        sa.Column("period", sa.String(10), nullable=False, server_default="month"),
        sa.Column("target_value", sa.BigInteger, nullable=False),
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
        sa.UniqueConstraint(
            "store_id", "metric", "period", name="uq_metric_targets_store_metric_period"
        ),
        schema="public",
    )
    op.create_index(
        "ix_metric_targets_store_id",
        "metric_targets",
        ["store_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_targets_store_id", table_name="metric_targets", schema="public"
    )
    op.drop_table("metric_targets", schema="public")

"""Add platform_benchmarks (public) — anonymous peer benchmark cells.

Aggregates only: percentile cells per (period, segment, metric).
Store-level values never leave the aggregation task, and cells are
published to merchants only when n_stores >= 10 (enforced at read
time so the table can accumulate while the platform grows).

Revision ID: platform_benchmarks_20260715
Revises: merchant_signals_20260714
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "platform_benchmarks_20260715"
down_revision: str | None = "merchant_signals_20260714"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "platform_benchmarks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("segment_key", sa.String(120), nullable=False),
        sa.Column("metric", sa.String(50), nullable=False),
        sa.Column("p25", sa.Float(), nullable=False),
        sa.Column("p50", sa.Float(), nullable=False),
        sa.Column("p75", sa.Float(), nullable=False),
        sa.Column("n_stores", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "period", "segment_key", "metric", name="uq_platform_benchmark_cell"
        ),
        schema="public",
    )
    op.create_index(
        "ix_platform_benchmarks_period",
        "platform_benchmarks",
        ["period"],
        schema="public",
    )
    op.create_index(
        "ix_platform_benchmarks_segment_key",
        "platform_benchmarks",
        ["segment_key"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_benchmarks_segment_key",
        table_name="platform_benchmarks",
        schema="public",
    )
    op.drop_index(
        "ix_platform_benchmarks_period",
        table_name="platform_benchmarks",
        schema="public",
    )
    op.drop_table("platform_benchmarks", schema="public")

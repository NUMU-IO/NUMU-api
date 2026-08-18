"""Create meta_match_quality_snapshot — the EMQ history table.

``MetaMatchQualityService.get_snapshots`` has returned a hardcoded ``[]`` since
it shipped, under a comment promising the table in "v1.1". That migration was
never written, ``poll_match_quality`` was never scheduled, and the hub rendered
a permanent empty state — so the platform could not report Event Match Quality
for any store. Every other signal-quality change is unfalsifiable without it.

Append-only history rather than one current row: Meta scores over a rolling
window, so proving a change worked means comparing the same event across polls.

Revision ID: meta_mq_snapshot_20260817
Revises: meta_pixel_dedup_20260817
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "meta_mq_snapshot_20260817"
down_revision: str | Sequence[str] | None = "meta_pixel_dedup_20260817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "meta_match_quality_snapshot"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pixel_id", sa.Text(), nullable=False),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("emq_score", sa.Numeric(3, 1), nullable=False),
        sa.Column("dedup_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("event_coverage", sa.Numeric(5, 2), nullable=True),
        sa.Column("total_events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "match_key_coverage",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "diagnostics", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column("data_freshness", sa.Text(), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="public",
        if_not_exists=True,
    )

    op.create_index(
        "idx_meta_mq_store",
        _TABLE,
        ["store_id"],
        schema="public",
        if_not_exists=True,
    )
    op.create_index(
        "idx_meta_mq_store_pixel_event_captured",
        _TABLE,
        ["store_id", "pixel_id", "event_name", "captured_at"],
        schema="public",
        if_not_exists=True,
    )
    op.create_index(
        "idx_meta_mq_captured_at",
        _TABLE,
        ["captured_at"],
        schema="public",
        if_not_exists=True,
    )

    # Same RLS posture as `meta_event_log` (20260427_add_meta_tracking):
    # ENABLE + FORCE, then one policy per operation keyed on
    # `public.get_current_tenant_id()`. Postgres enforces isolation; the
    # repository's tenant filter is defense in depth.
    conn = op.get_bind()
    conn.exec_driver_sql(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql(f"ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY")
    for name, clause in (
        ("select", "FOR SELECT USING (tenant_id = public.get_current_tenant_id())"),
        (
            "insert",
            "FOR INSERT WITH CHECK (tenant_id = public.get_current_tenant_id())",
        ),
        (
            "update",
            "FOR UPDATE USING (tenant_id = public.get_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.get_current_tenant_id())",
        ),
        ("delete", "FOR DELETE USING (tenant_id = public.get_current_tenant_id())"),
    ):
        conn.exec_driver_sql(
            f"DROP POLICY IF EXISTS tenant_isolation_{name} ON public.{_TABLE}"
        )
        conn.exec_driver_sql(
            f"CREATE POLICY tenant_isolation_{name} ON public.{_TABLE} {clause}"
        )


def downgrade() -> None:
    conn = op.get_bind()
    for name in ("select", "insert", "update", "delete"):
        conn.exec_driver_sql(
            f"DROP POLICY IF EXISTS tenant_isolation_{name} ON public.{_TABLE}"
        )
    op.drop_index("idx_meta_mq_captured_at", _TABLE, schema="public")
    op.drop_index("idx_meta_mq_store_pixel_event_captured", _TABLE, schema="public")
    op.drop_index("idx_meta_mq_store", _TABLE, schema="public")
    op.drop_table(_TABLE, schema="public")

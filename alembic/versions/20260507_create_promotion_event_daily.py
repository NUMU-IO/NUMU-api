"""Create promotion_event_daily aggregate table.

Pre-aggregated counters that the merchant analytics endpoint reads
in O(days) instead of scanning the append-only `promotion_events`
table for every request. Populated nightly by the
`tasks.rollup_promotion_events_daily` Celery task; for "today"
data the analytics endpoint merges the daily rollup with a live
aggregation over `promotion_events` so freshness is preserved.

Tenant-scoped + RLS-protected like every other promo table.

Revision ID: promo_daily_20260507
Revises: promo_events_20260507
Create Date: 2026-05-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "promo_daily_20260507"
down_revision: str | None = "promo_events_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_TABLES = ["promotion_event_daily"]


def _apply_rls(table: str) -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;")
    conn.exec_driver_sql(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;")
    conn.exec_driver_sql(f"""
        CREATE POLICY tenant_isolation_select ON public.{table}
            FOR SELECT
            USING (tenant_id = public.get_current_tenant_id());
    """)
    conn.exec_driver_sql(f"""
        CREATE POLICY tenant_isolation_insert ON public.{table}
            FOR INSERT
            WITH CHECK (tenant_id = public.get_current_tenant_id());
    """)
    conn.exec_driver_sql(f"""
        CREATE POLICY tenant_isolation_update ON public.{table}
            FOR UPDATE
            USING (tenant_id = public.get_current_tenant_id())
            WITH CHECK (tenant_id = public.get_current_tenant_id());
    """)
    conn.exec_driver_sql(f"""
        CREATE POLICY tenant_isolation_delete ON public.{table}
            FOR DELETE
            USING (tenant_id = public.get_current_tenant_id());
    """)
    conn.exec_driver_sql(f"""
        CREATE POLICY admin_bypass ON public.{table}
            FOR ALL
            USING (public.is_rls_bypassed() = true)
            WITH CHECK (public.is_rls_bypassed() = true);
    """)


def _drop_rls(table: str) -> None:
    conn = op.get_bind()
    for policy in (
        "tenant_isolation_select",
        "tenant_isolation_insert",
        "tenant_isolation_update",
        "tenant_isolation_delete",
        "admin_bypass",
    ):
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS {policy} ON public.{table};")
    conn.exec_driver_sql(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;")


def upgrade() -> None:
    event_type_enum = postgresql.ENUM(
        name="event_type_enum",
        schema="public",
        create_type=False,
    )

    op.create_table(
        "promotion_event_daily",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.promotions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("event_type", event_type_enum, nullable=False),
        sa.Column(
            "count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "unique_visitors",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "discount_total_cents",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "revenue_cents",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rolled_up_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "promotion_id",
            "day",
            "event_type",
            name="pk_promotion_event_daily",
        ),
        schema="public",
    )

    op.create_index(
        "ix_promo_daily_tenant_id",
        "promotion_event_daily",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_promo_daily_store_day",
        "promotion_event_daily",
        ["store_id", "day"],
        schema="public",
    )

    for table in _RLS_TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in _RLS_TABLES:
        _drop_rls(table)
    op.drop_index(
        "ix_promo_daily_store_day",
        table_name="promotion_event_daily",
        schema="public",
    )
    op.drop_index(
        "ix_promo_daily_tenant_id",
        table_name="promotion_event_daily",
        schema="public",
    )
    op.drop_table("promotion_event_daily", schema="public")

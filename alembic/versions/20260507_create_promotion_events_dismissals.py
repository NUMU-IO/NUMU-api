"""Create promotion_events (append-only) and promotion_dismissals tables.

`promotion_events` is the time-series log that powers all analytics
(impression / click / dismiss / redeem / convert). The `occurred_at`
column carries a BRIN index — the table is append-only and inserts
arrive in time order, so BRIN is dramatically cheaper than B-tree.

`promotion_dismissals` records per-customer or per-anonymous-visitor
suppression so the same shopper isn't nagged. Two partial unique
indexes enforce one row per (promotion, customer) and one per
(promotion, visitor_token) without colliding when the other is NULL.

A CHECK constraint guarantees that exactly one of `customer_id` or
`visitor_token` is set on every row.

RLS follows the four-policy + admin_bypass pattern.

Revision ID: promo_events_20260507
Revises: promo_displays_20260507
Create Date: 2026-05-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "promo_events_20260507"
down_revision: str | None = "promo_displays_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_TABLES = ["promotion_events", "promotion_dismissals"]


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
        "promotion_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
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
        sa.Column("event_type", event_type_enum, nullable=False),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("discount_amount_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="public",
    )

    op.create_index(
        "ix_promotion_events_tenant_id",
        "promotion_events",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_events_store_id",
        "promotion_events",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_events_promotion_id",
        "promotion_events",
        ["promotion_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_events_promo_type_time",
        "promotion_events",
        ["promotion_id", "event_type", "occurred_at"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_events_store_time",
        "promotion_events",
        ["store_id", "occurred_at"],
        schema="public",
    )
    # BRIN — cheap, time-ordered inserts.
    op.create_index(
        "ix_promotion_events_occurred_at_brin",
        "promotion_events",
        ["occurred_at"],
        schema="public",
        postgresql_using="brin",
    )

    op.create_table(
        "promotion_dismissals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.promotions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("visitor_token", sa.String(length=64), nullable=True),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(customer_id IS NOT NULL) <> (visitor_token IS NOT NULL)",
            name="ck_promotion_dismissals_subject_xor",
        ),
        schema="public",
    )

    op.create_index(
        "ix_promotion_dismissals_tenant_id",
        "promotion_dismissals",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_promotion_dismissals_promotion_id",
        "promotion_dismissals",
        ["promotion_id"],
        schema="public",
    )
    # Partial unique indexes — one row per subject per promotion.
    op.create_index(
        "uq_promotion_dismissals_promo_customer",
        "promotion_dismissals",
        ["promotion_id", "customer_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )
    op.create_index(
        "uq_promotion_dismissals_promo_visitor",
        "promotion_dismissals",
        ["promotion_id", "visitor_token"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("visitor_token IS NOT NULL"),
    )

    for table in _RLS_TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in _RLS_TABLES:
        _drop_rls(table)

    for idx in (
        "uq_promotion_dismissals_promo_visitor",
        "uq_promotion_dismissals_promo_customer",
        "ix_promotion_dismissals_promotion_id",
        "ix_promotion_dismissals_tenant_id",
    ):
        op.drop_index(idx, table_name="promotion_dismissals", schema="public")
    op.drop_table("promotion_dismissals", schema="public")

    for idx in (
        "ix_promotion_events_occurred_at_brin",
        "ix_promotion_events_store_time",
        "ix_promotion_events_promo_type_time",
        "ix_promotion_events_promotion_id",
        "ix_promotion_events_store_id",
        "ix_promotion_events_tenant_id",
    ):
        op.drop_index(idx, table_name="promotion_events", schema="public")
    op.drop_table("promotion_events", schema="public")

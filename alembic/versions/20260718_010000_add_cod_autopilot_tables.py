"""Add COD Autopilot tables (delivery checks + ship digests) with RLS.

Feature 004-cod-autopilot. Two tenant-scoped tables:

- ``whatsapp_delivery_checks`` — one row per Autopilot-eligible shipped
  order; drives the customer delivery-check conversation (attempts,
  retries, response, outcome) plus the assumed-delivered fallback timer
  and the merchant exception queue.
- ``whatsapp_ship_digests`` — one row per store per local day; records
  exactly which orders the daily merchant ship-digest listed, so an
  inbound "All shipped" tap / exceptions reply can only ever act on the
  orders that were actually in the message (FR-005/FR-009), exactly once
  (FR-008, ``processed_at``).

RLS policies ship in the SAME migration as the tables (Constitution V),
mirroring ``_add_rls_for_table`` from wa_optin_sched_dl_20260524.

Revision ID: cod_autopilot_20260718
Revises: platform_benchmarks_20260715
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "cod_autopilot_20260718"
down_revision: str | Sequence[str] | None = "platform_benchmarks_20260715"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_rls_for_table(table_name: str) -> None:
    """Enable RLS mirroring the central pattern in 20260203_add_rls_policies.py
    (4 per-op tenant policies + admin_bypass)."""
    conn = op.get_bind()

    conn.exec_driver_sql(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY;")
    conn.exec_driver_sql(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY;")
    conn.exec_driver_sql(
        f"""
        CREATE POLICY tenant_isolation_select ON public.{table_name}
            FOR SELECT
            USING (tenant_id = public.get_current_tenant_id());
        """
    )
    conn.exec_driver_sql(
        f"""
        CREATE POLICY tenant_isolation_insert ON public.{table_name}
            FOR INSERT
            WITH CHECK (tenant_id = public.get_current_tenant_id());
        """
    )
    conn.exec_driver_sql(
        f"""
        CREATE POLICY tenant_isolation_update ON public.{table_name}
            FOR UPDATE
            USING (tenant_id = public.get_current_tenant_id())
            WITH CHECK (tenant_id = public.get_current_tenant_id());
        """
    )
    conn.exec_driver_sql(
        f"""
        CREATE POLICY tenant_isolation_delete ON public.{table_name}
            FOR DELETE
            USING (tenant_id = public.get_current_tenant_id());
        """
    )
    conn.exec_driver_sql(
        f"""
        CREATE POLICY admin_bypass ON public.{table_name}
            FOR ALL
            USING (public.is_rls_bypassed() = true)
            WITH CHECK (public.is_rls_bypassed() = true);
        """
    )


def _drop_rls_for_table(table_name: str) -> None:
    conn = op.get_bind()
    for policy in (
        "admin_bypass",
        "tenant_isolation_delete",
        "tenant_isolation_update",
        "tenant_isolation_insert",
        "tenant_isolation_select",
    ):
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS {policy} ON public.{table_name};")
    conn.exec_driver_sql(f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY;")


def upgrade() -> None:
    # ─── 1. whatsapp_delivery_checks ─────────────────────────────────
    op.create_table(
        "whatsapp_delivery_checks",
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
            index=True,
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
            unique=True,
        ),
        sa.Column("customer_phone", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        # none | received | not_yet | refused (last customer response)
        sa.Column("response", sa.String(20), nullable=False, server_default="none"),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        # pending | delivered_confirmed | response_exhausted | assumed_delivered
        # | exception | superseded
        sa.Column("outcome", sa.String(30), nullable=False, server_default="pending"),
        # refused | response_exhausted | late_contradiction
        sa.Column("exception_reason", sa.String(30), nullable=True),
        sa.Column("exception_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "assumed_delivered_due_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema="public",
    )
    op.create_index(
        "ix_wa_delivery_checks_next_attempt",
        "whatsapp_delivery_checks",
        ["next_attempt_at"],
        schema="public",
    )
    op.create_index(
        "ix_wa_delivery_checks_outcome",
        "whatsapp_delivery_checks",
        ["outcome"],
        schema="public",
    )
    op.create_index(
        "ix_wa_delivery_checks_fallback_due",
        "whatsapp_delivery_checks",
        ["assumed_delivered_due_at"],
        schema="public",
    )
    _add_rls_for_table("whatsapp_delivery_checks")

    # ─── 2. whatsapp_ship_digests ────────────────────────────────────
    op.create_table(
        "whatsapp_ship_digests",
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
            index=True,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("digest_date", sa.Date, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("merchant_phone", sa.String(20), nullable=False),
        # [{"n": 1, "order_id": "...", "order_number": "..."}] — the ONLY
        # orders an inbound digest response may act on (max 10, R-09).
        sa.Column("order_items", JSONB, nullable=False),
        sa.Column("capped_count", sa.Integer, nullable=False, server_default="0"),
        # none | all_shipped | exceptions
        sa.Column(
            "response_type", sa.String(20), nullable=False, server_default="none"
        ),
        sa.Column("response_raw", sa.Text, nullable=True),
        sa.Column("excepted_numbers", JSONB, nullable=True),
        # Set exactly once — presence means the digest was consumed (FR-008).
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "store_id", "digest_date", name="uq_wa_ship_digests_store_date"
        ),
        schema="public",
    )
    op.create_index(
        "ix_wa_ship_digests_merchant_phone",
        "whatsapp_ship_digests",
        ["merchant_phone"],
        schema="public",
    )
    _add_rls_for_table("whatsapp_ship_digests")


def downgrade() -> None:
    _drop_rls_for_table("whatsapp_ship_digests")
    op.drop_index(
        "ix_wa_ship_digests_merchant_phone",
        table_name="whatsapp_ship_digests",
        schema="public",
    )
    op.drop_table("whatsapp_ship_digests", schema="public")

    _drop_rls_for_table("whatsapp_delivery_checks")
    op.drop_index(
        "ix_wa_delivery_checks_fallback_due",
        table_name="whatsapp_delivery_checks",
        schema="public",
    )
    op.drop_index(
        "ix_wa_delivery_checks_outcome",
        table_name="whatsapp_delivery_checks",
        schema="public",
    )
    op.drop_index(
        "ix_wa_delivery_checks_next_attempt",
        table_name="whatsapp_delivery_checks",
        schema="public",
    )
    op.drop_table("whatsapp_delivery_checks", schema="public")

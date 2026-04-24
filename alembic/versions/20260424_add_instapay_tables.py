"""Add instapay_intents and payment_proofs tables.

Supports the InstaPay manual-verification checkout flow: one intent per
order (ref code + QR payload + expiry), and one-or-many payment proofs
per order (customer screenshot + transaction ref + review decision).

Both tables carry tenant_id and are wrapped in the standard four-policy
RLS block plus the admin_bypass policy, mirroring the pattern from
`20260203_add_rls_policies.py`.

Revision ID: instapay_tables_20260424
Revises: cust_addr_loc_20260422
Create Date: 2026-04-24 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "instapay_tables_20260424"
down_revision: str | None = "cust_addr_loc_20260422"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_TABLES = ["instapay_intents", "payment_proofs"]


def _apply_rls(table: str) -> None:
    """Replicate the tenant_isolation + admin_bypass policy bundle.

    Same shape as `20260203_add_rls_policies.py` — four per-operation
    policies keyed on `public.get_current_tenant_id()`, plus the
    `admin_bypass` escape hatch for reconciliation jobs that run with
    `app.rls_bypass = 'true'`.
    """
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
    intent_status_enum = postgresql.ENUM(
        "awaiting_payment",
        "proof_received",
        "paid",
        "expired",
        "cancelled",
        name="instapay_intent_status_enum",
        schema="public",
        create_type=True,
    )
    proof_status_enum = postgresql.ENUM(
        "awaiting_review",
        "auto_approved",
        "approved",
        "rejected",
        "expired",
        name="payment_proof_status_enum",
        schema="public",
        create_type=True,
    )

    bind = op.get_bind()
    intent_status_enum.create(bind, checkfirst=True)
    proof_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "instapay_intents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
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
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reference_code", sa.String(16), nullable=False),
        sa.Column("display_ipa", sa.String(80), nullable=False),
        sa.Column("display_phone", sa.String(20), nullable=True),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("qr_payload", sa.Text, nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                name="instapay_intent_status_enum",
                schema="public",
                create_type=False,
            ),
            nullable=False,
            server_default="awaiting_payment",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="public",
    )
    op.create_index(
        "ix_instapay_intents_tenant_id",
        "instapay_intents",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_instapay_intents_store_id",
        "instapay_intents",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_instapay_intents_order_id",
        "instapay_intents",
        ["order_id"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_instapay_intents_reference_code",
        "instapay_intents",
        ["reference_code"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_instapay_intents_status_expires",
        "instapay_intents",
        ["status", "expires_at"],
        schema="public",
    )

    op.create_table(
        "payment_proofs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
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
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proof_image_key", sa.Text, nullable=False),
        sa.Column("proof_image_hash", sa.LargeBinary, nullable=False),
        sa.Column("transaction_ref", sa.String(64), nullable=False),
        sa.Column("declared_amount_cents", sa.Integer, nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                name="payment_proof_status_enum",
                schema="public",
                create_type=False,
            ),
            nullable=False,
            server_default="awaiting_review",
        ),
        sa.Column(
            "review_decision_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "review_decision_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "store_id",
            "proof_image_hash",
            name="uq_payment_proofs_store_image_hash",
        ),
        sa.UniqueConstraint(
            "store_id",
            "transaction_ref",
            name="uq_payment_proofs_store_transaction_ref",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_payment_proofs_idempotency_key",
        ),
        schema="public",
    )
    op.create_index(
        "ix_payment_proofs_tenant_id",
        "payment_proofs",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_payment_proofs_store_id",
        "payment_proofs",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_payment_proofs_order_id",
        "payment_proofs",
        ["order_id"],
        schema="public",
    )
    op.create_index(
        "ix_payment_proofs_status",
        "payment_proofs",
        ["status"],
        schema="public",
    )
    op.create_index(
        "ix_payment_proofs_store_created",
        "payment_proofs",
        ["store_id", "created_at"],
        schema="public",
    )

    for table in _RLS_TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in _RLS_TABLES:
        _drop_rls(table)

    for idx in (
        "ix_payment_proofs_store_created",
        "ix_payment_proofs_status",
        "ix_payment_proofs_order_id",
        "ix_payment_proofs_store_id",
        "ix_payment_proofs_tenant_id",
    ):
        op.drop_index(idx, table_name="payment_proofs", schema="public")
    op.drop_table("payment_proofs", schema="public")

    for idx in (
        "ix_instapay_intents_status_expires",
        "ix_instapay_intents_reference_code",
        "ix_instapay_intents_order_id",
        "ix_instapay_intents_store_id",
        "ix_instapay_intents_tenant_id",
    ):
        op.drop_index(idx, table_name="instapay_intents", schema="public")
    op.drop_table("instapay_intents", schema="public")

    bind = op.get_bind()
    postgresql.ENUM(
        name="payment_proof_status_enum",
        schema="public",
    ).drop(bind, checkfirst=True)
    postgresql.ENUM(
        name="instapay_intent_status_enum",
        schema="public",
    ).drop(bind, checkfirst=True)

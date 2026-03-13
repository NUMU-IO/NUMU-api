"""Add payment reconciliation tables.

Revision ID: aa9988776655
Revises: ff0011223344
Create Date: 2026-03-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "aa9988776655"
down_revision: str | None = "ff0011223344"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Reconciliation runs ---
    op.create_table(
        "payment_reconciliation_runs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("gateway", sa.String(50), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "total_orders_checked", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_transactions_checked",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("mismatches_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "expected_amount_cents", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "actual_amount_cents", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        "idx_recon_runs_period",
        "payment_reconciliation_runs",
        ["period_start", "period_end"],
        schema="public",
    )

    # --- Reconciliation mismatches ---
    op.create_table(
        "reconciliation_mismatches",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mismatch_type", sa.String(50), nullable=False),
        sa.Column("order_id", UUID(as_uuid=True), nullable=True),
        sa.Column("order_number", sa.String(50), nullable=True),
        sa.Column("transaction_id", UUID(as_uuid=True), nullable=True),
        sa.Column("gateway_transaction_id", sa.String(255), nullable=True),
        sa.Column("expected_amount_cents", sa.Integer(), nullable=True),
        sa.Column("actual_amount_cents", sa.Integer(), nullable=True),
        sa.Column("gateway", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
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
        "idx_recon_mismatches_run_id",
        "reconciliation_mismatches",
        ["run_id"],
        schema="public",
    )
    op.create_index(
        "idx_recon_mismatches_resolved",
        "reconciliation_mismatches",
        ["resolved"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_recon_mismatches_resolved",
        table_name="reconciliation_mismatches",
        schema="public",
    )
    op.drop_index(
        "idx_recon_mismatches_run_id",
        table_name="reconciliation_mismatches",
        schema="public",
    )
    op.drop_table("reconciliation_mismatches", schema="public")
    op.drop_index(
        "idx_recon_runs_period",
        table_name="payment_reconciliation_runs",
        schema="public",
    )
    op.drop_table("payment_reconciliation_runs", schema="public")

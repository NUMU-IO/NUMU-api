"""Add COD deposit-to-confirm fields to orders + PENDING_DEPOSIT status.

Columns added to `orders`:
    deposit_required_cents   — merchant policy snapshot at checkout time
    deposit_amount_cents     — amount actually collected (matches required on happy path)
    deposit_paid_at          — timestamp the gateway confirmed the deposit
    deposit_expires_at       — cut-off after which the background sweeper cancels the order
    deposit_gateway          — one of paymob | kashier | fawry | fawaterak | instapay
    deposit_payment_id       — external transaction ID from the gateway

Enum change:
    `orderstatus` gains the value `pending_deposit`.

Enum additions can't be rolled back cleanly in PostgreSQL — `ALTER TYPE
... DROP VALUE` doesn't exist. The downgrade here NULLs out any orders
stuck in the new status (so the enum itself keeps working) and drops
the new columns. The enum value itself lingers forever; that's
tolerable because it's semantically frozen.

Revision ID: cod_deposit_fields_20260425
Revises: scope_instapay_idem_20260424
Create Date: 2026-04-25 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "cod_deposit_fields_20260425"
down_revision: str | None = "scope_instapay_idem_20260424"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Enum value addition ────────────────────────────────────
    # Has to run outside the transactional DDL block that wraps the
    # rest of the migration. COMMIT the current tx, run ADD VALUE, then
    # start a fresh block for the column adds. This mirrors how the
    # other alembic migrations in this repo sidestep the "ALTER TYPE
    # ADD VALUE can't run in a transaction" restriction.
    conn = op.get_bind()
    # `ADD VALUE IF NOT EXISTS` needs PG ≥ 9.6, which NUMU is well past.
    # Running it as a COMMIT-prefixed statement so the enum update
    # escapes the Alembic outer transaction on our managed connection.
    conn.exec_driver_sql("COMMIT")
    conn.exec_driver_sql(
        "ALTER TYPE public.orderstatus ADD VALUE IF NOT EXISTS 'pending_deposit'"
    )
    # Start a new transaction so the column adds participate in normal
    # DDL rollback semantics.
    conn.exec_driver_sql("BEGIN")

    # ── 2. Order columns ──────────────────────────────────────────
    op.add_column(
        "orders",
        sa.Column("deposit_required_cents", sa.Integer, nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column("deposit_amount_cents", sa.Integer, nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column(
            "deposit_paid_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column(
            "deposit_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column("deposit_gateway", sa.String(32), nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column("deposit_payment_id", sa.String(255), nullable=True),
        schema="public",
    )
    # Index on deposit_expires_at — the background sweeper scans
    # `WHERE status = 'pending_deposit' AND deposit_expires_at <= now()`.
    op.create_index(
        "ix_orders_deposit_expires_at",
        "orders",
        ["deposit_expires_at"],
        schema="public",
        postgresql_where=sa.text("deposit_expires_at IS NOT NULL"),
    )


def downgrade() -> None:
    # Rescue any orders stranded in the new status before we drop the
    # columns that support it. They become plain PENDING — data loss
    # is limited to the deposit metadata.
    conn = op.get_bind()
    conn.exec_driver_sql(
        "UPDATE public.orders SET status = 'pending' WHERE status = 'pending_deposit'"
    )

    op.drop_index("ix_orders_deposit_expires_at", table_name="orders", schema="public")
    op.drop_column("orders", "deposit_payment_id", schema="public")
    op.drop_column("orders", "deposit_gateway", schema="public")
    op.drop_column("orders", "deposit_expires_at", schema="public")
    op.drop_column("orders", "deposit_paid_at", schema="public")
    op.drop_column("orders", "deposit_amount_cents", schema="public")
    op.drop_column("orders", "deposit_required_cents", schema="public")

    # Enum values can't be DROP'd in Postgres — the value lives on
    # forever in `orderstatus`. Future re-upgrade is a no-op thanks
    # to the IF NOT EXISTS guard in `upgrade()`.
    _ = postgresql  # keep the import for future use; Alembic quirk

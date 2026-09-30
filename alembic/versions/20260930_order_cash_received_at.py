"""COD: when the courier's cash reached the merchant.

Revision ID: order_cash_received_20260930
Revises: proof_amount_backfill_20260930
Create Date: 2026-09-30

A COD order is paid when the customer pays the courier at the door, but the
merchant only has the money once the courier remits it, days later. The
order stays PAID either way; ``cash_received_at`` records the second step so
the hub can show "paid, with courier" vs "paid, collected". Additive and
nullable: every existing order reads as not-yet-received until the merchant
marks it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "order_cash_received_20260930"
down_revision: str | Sequence[str] | None = "proof_amount_backfill_20260930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("cash_received_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("orders", "cash_received_at", schema="public")

"""orders.collected_total — cash actually collected after a partial acceptance.

NULL means "the full total" everywhere (reconciliation, commission,
refundable amount, shipment COD), so existing rows are untouched.

Idempotent: ADD COLUMN IF NOT EXISTS, safe to re-run.

Revision ID: orders_collected_total_20260823
Revises: merchant_notifs_20260822
Create Date: 2026-08-23
"""

from alembic import op

revision = "orders_collected_total_20260823"
down_revision = "merchant_notifs_20260822"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS collected_total INTEGER"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.orders DROP COLUMN IF EXISTS collected_total")

"""COD deposit amount = what was actually approved, not what was asked.

Revision ID: deposit_amount_actual_20260930
Revises: order_cash_received_20260930
Create Date: 2026-09-30

Checkout stamps ``orders.deposit_amount_cents`` with the REQUIRED deposit,
and approving an InstaPay / Vodafone Cash deposit proof never replaced it
with the approved amount. A customer who sent 1,000 against a 1,165 deposit
showed "Deposit: 1,165 paid". Approval now writes the approved amount; this
corrects deposits approved before that from their approved customer proofs.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "deposit_amount_actual_20260930"
down_revision: str | Sequence[str] | None = "order_cash_received_20260930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE public.orders AS o "
        "SET deposit_amount_cents = p.paid "
        "FROM ("
        "  SELECT order_id, SUM(declared_amount_cents) AS paid "
        "  FROM public.payment_proofs "
        "  WHERE recorded_method IS NULL "
        "  AND declared_amount_cents IS NOT NULL "
        "  AND status IN ('approved', 'auto_approved') "
        "  GROUP BY order_id"
        ") AS p "
        "WHERE p.order_id = o.id "
        "AND o.deposit_paid_at IS NOT NULL "
        "AND o.deposit_amount_cents IS DISTINCT FROM p.paid"
    )


def downgrade() -> None:
    pass

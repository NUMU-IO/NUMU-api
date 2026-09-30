"""Give approved customer proofs the amount they collected.

Revision ID: proof_amount_backfill_20260930
Revises: cod_review_hold_20260925
Create Date: 2026-09-30

Approving a customer's InstaPay / Vodafone Cash proof never stored an
amount on the proof, so the order page summed it as 0 toward what's paid.
For a COD deposit that left the full total showing as due, and the merchant
tried to record the same receipt again. Approval now stores the intent's
amount; this fills the rows approved before that.

The intent is unique per order, so the join is one-to-one.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "proof_amount_backfill_20260930"
down_revision: str | Sequence[str] | None = "cod_review_hold_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE public.payment_proofs AS p "
        "SET declared_amount_cents = i.amount_cents "
        "FROM public.instapay_intents AS i "
        "WHERE i.order_id = p.order_id "
        "AND p.recorded_method IS NULL "
        "AND p.declared_amount_cents IS NULL "
        "AND p.status IN ('approved', 'auto_approved')"
    )


def downgrade() -> None:
    pass

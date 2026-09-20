"""Let a voided merchant payment release its transaction reference.

Revision ID: proof_transaction_ref_partial_20260920
Revises: proof_image_hash_partial_20260920
Create Date: 2026-09-20

``uq_payment_proofs_store_transaction_ref`` was a plain unique constraint on
(store_id, transaction_ref), which broke the documented typo-correction path:
the merchant records a payment with a rail reference, voids it, and
re-records it with the SAME reference — the only one they have. The pre-check
returned 409 and, if it had not, the INSERT would have failed on the
constraint.

Replaced with a partial unique index carrying the same columns and excluding
only voided merchant-recorded rows, in step with
``PaymentProofRepository.transaction_ref_exists``. A rejected customer proof
is still covered: its reference stays blocked, matching the image-hash rule.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "proof_transaction_ref_partial_20260920"
down_revision: str | Sequence[str] | None = "proof_image_hash_partial_20260920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARTIAL_WHERE = "NOT (recorded_method IS NOT NULL AND status = 'rejected')"


def upgrade() -> None:
    op.drop_constraint(
        "uq_payment_proofs_store_transaction_ref",
        "payment_proofs",
        schema="public",
        type_="unique",
    )
    op.create_index(
        "uq_payment_proofs_store_transaction_ref",
        "payment_proofs",
        ["store_id", "transaction_ref"],
        unique=True,
        schema="public",
        postgresql_where=sa.text(_PARTIAL_WHERE),
    )


def downgrade() -> None:
    # Reverting can fail if a merchant has already voided and re-recorded
    # with the same reference: two rows would then share (store_id,
    # transaction_ref) and the full constraint has nothing to collapse them
    # to. Deliberate one-way door — the data is correct, the old constraint
    # is what was wrong.
    op.drop_index(
        "uq_payment_proofs_store_transaction_ref",
        table_name="payment_proofs",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_payment_proofs_store_transaction_ref",
        "payment_proofs",
        ["store_id", "transaction_ref"],
        schema="public",
    )

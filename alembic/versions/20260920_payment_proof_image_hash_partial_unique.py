"""Let a voided merchant payment release its receipt image hash.

Revision ID: proof_image_hash_partial_20260920
Revises: proof_recorded_method_20260920
Create Date: 2026-09-20

``uq_payment_proofs_store_image_hash`` was a plain unique constraint on
(store_id, proof_image_hash), which made one legitimate case impossible: the
merchant mistypes an amount, voids the payment, and re-records it from the
SAME receipt. There is only one receipt for that transfer, so the merchant had
no way to correct the mistake — the pre-check returned 409 and, if it had not,
the INSERT would have failed on the constraint.

Replaced with a partial unique index carrying the same columns and excluding
only voided merchant-recorded rows. A customer whose proof was rejected is
still covered: resubmitting identical bytes after a rejection is exactly the
screenshot-replay attack the uniqueness exists to stop.

Kept in step with ``PaymentProofRepository.image_hash_exists``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "proof_image_hash_partial_20260920"
down_revision: str | Sequence[str] | None = "proof_recorded_method_20260920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARTIAL_WHERE = "NOT (recorded_method IS NOT NULL AND status = 'rejected')"


def upgrade() -> None:
    op.drop_constraint(
        "uq_payment_proofs_store_image_hash",
        "payment_proofs",
        schema="public",
        type_="unique",
    )
    op.create_index(
        "uq_payment_proofs_store_image_hash",
        "payment_proofs",
        ["store_id", "proof_image_hash"],
        unique=True,
        schema="public",
        postgresql_where=sa.text(_PARTIAL_WHERE),
    )


def downgrade() -> None:
    # Reverting can fail if a merchant has already voided and re-recorded the
    # same receipt: two rows would then share (store_id, proof_image_hash) and
    # the full constraint has nothing to collapse them to. That is a deliberate
    # one-way door — the data is correct, the old constraint is what was wrong.
    op.drop_index(
        "uq_payment_proofs_store_image_hash",
        table_name="payment_proofs",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_payment_proofs_store_image_hash",
        "payment_proofs",
        ["store_id", "proof_image_hash"],
        schema="public",
    )

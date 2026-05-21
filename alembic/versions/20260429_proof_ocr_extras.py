"""Add note / transaction-ref / recipient-name OCR fields to payment_proofs.

Revision ID: proof_ocr_extras_20260429
Revises: proof_ocr_20260428
Create Date: 2026-04-29

Phase C extension. Three more nullable columns persisting what the
OCR provider read from a proof screenshot:

  * ``ocr_extracted_note`` — text from the bank app's "Note" / "Reason"
    section. The customer is asked to paste our intent reference code
    here; the new ``ocr_note_missing_reference`` rule looks for a
    substring match.
  * ``ocr_extracted_transaction_ref`` — the bank's transaction ID as
    OCR'd. Cross-checks against what the customer typed into the
    proof-upload form (``transaction_ref``); mismatch → soft block.
  * ``ocr_extracted_recipient_name`` — text near the "To" anchor on
    the receipt, used for forensics + the ``recipient_name`` rule.
    Most banks mask all but the first 1–2 visible characters; the
    rule matches a merchant-supplied token (typically a first name).

All nullable so older rows keep working; rules only fire when
``ocr_status == "ok"`` AND the merchant has opted into the matching
flag — same gating posture as Phase C's existing OCR rules.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "proof_ocr_extras_20260429"
down_revision: str | None = "proof_ocr_20260428"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_proofs",
        sa.Column("ocr_extracted_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "payment_proofs",
        sa.Column(
            "ocr_extracted_transaction_ref",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "payment_proofs",
        sa.Column("ocr_extracted_recipient_name", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_proofs", "ocr_extracted_recipient_name")
    op.drop_column("payment_proofs", "ocr_extracted_transaction_ref")
    op.drop_column("payment_proofs", "ocr_extracted_note")

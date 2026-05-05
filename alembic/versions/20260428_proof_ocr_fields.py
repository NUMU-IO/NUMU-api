"""Add OCR fields to payment_proofs.

Revision ID: proof_ocr_20260428
Revises: proof_phash_20260427
Create Date: 2026-04-28

Phase C of the smarter image-processing plan
([linear-toasting-horizon.md]). Adds six nullable columns that hold
the result of running an OCR provider over the sanitized proof image.
The auto-approval engine reads these (when the merchant opts in) to
soft-block proofs whose extracted amount or recipient IPA disagree
with the order they're attached to.

All columns are nullable: rows predating Phase C carry NULLs and the
new auto-approval rules silently no-op for them. ``ocr_status`` is a
free-form TEXT (not an enum) so we can grow the value space — e.g.
add a ``"timeout"`` discriminator — without another DDL migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "proof_ocr_20260428"
down_revision: str | None = "proof_phash_20260427"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_proofs",
        sa.Column("ocr_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "payment_proofs",
        sa.Column("ocr_extracted_amount_cents", sa.Integer(), nullable=True),
    )
    op.add_column(
        "payment_proofs",
        sa.Column("ocr_extracted_ipa", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "payment_proofs",
        sa.Column("ocr_raw_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "payment_proofs",
        sa.Column("ocr_provider", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "payment_proofs",
        sa.Column(
            "ocr_processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("payment_proofs", "ocr_processed_at")
    op.drop_column("payment_proofs", "ocr_provider")
    op.drop_column("payment_proofs", "ocr_raw_text")
    op.drop_column("payment_proofs", "ocr_extracted_ipa")
    op.drop_column("payment_proofs", "ocr_extracted_amount_cents")
    op.drop_column("payment_proofs", "ocr_status")

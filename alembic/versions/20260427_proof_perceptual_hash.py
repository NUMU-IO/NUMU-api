"""Add perceptual_hash column + per-store index to payment_proofs.

Revision ID: proof_phash_20260427
Revises: merge_heads_20260426
Create Date: 2026-04-27

Phase A of the smarter image-processing plan
([linear-toasting-horizon.md]). The new column carries a 64-bit pHash
of the *sanitized* proof image so the dedup layer can catch trivially
mutated reuploads (re-saves, 1-px crops) that defeat raw SHA-256.

Backfill is intentionally skipped — null means "predates pHash"; the
similarity check just won't match those rows. Every new upload after
this migration writes a non-null hash.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "proof_phash_20260427"
down_revision: str | None = "merge_heads_20260426"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_proofs",
        sa.Column("perceptual_hash", sa.BigInteger(), nullable=True),
    )
    # Composite index on (store_id, perceptual_hash) — narrows the
    # per-store recent-window scan that ``find_perceptual_neighbours``
    # uses to a covering range. Postgres has no native Hamming-distance
    # index without an extension; we compute distances in Python on the
    # ≤500-row window the index returns, which is ~1 ms.
    op.create_index(
        "ix_payment_proofs_store_phash",
        "payment_proofs",
        ["store_id", "perceptual_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_payment_proofs_store_phash", table_name="payment_proofs")
    op.drop_column("payment_proofs", "perceptual_hash")

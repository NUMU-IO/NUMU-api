"""Persist auto-approval block reasons on payment_proofs.

Revision ID: proof_block_reasons_20260430
Revises: proof_ocr_extras_20260429
Create Date: 2026-04-30

Today the rules engine produces ``decision.reasons`` (e.g.
``["ocr_amount_mismatch", "amount_above_auto_approve_threshold"]``) but
only logs them and emits a Prometheus counter; the merchant reviewing
the proof has no way to see *why* auto-approval didn't fire. This
column persists the list so the review pane can render a friendly
"Auto-approval blocked because…" panel.

Stored as ``TEXT[]`` because the tag set is small, fixed, and we want
ad-hoc analytics queries (``unnest(...) GROUP BY``) without a join
table. Nullable: an approved proof and pre-Phase-D rows both carry
NULL and the UI hides the panel.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision: str = "proof_block_reasons_20260430"
down_revision: str | None = "proof_ocr_extras_20260429"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_proofs",
        sa.Column(
            "auto_approval_block_reasons",
            ARRAY(sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("payment_proofs", "auto_approval_block_reasons")

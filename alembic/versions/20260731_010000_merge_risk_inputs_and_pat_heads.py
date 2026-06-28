"""Merge the risk-decision-inputs and personal-access-tokens heads.

Revision ID: merge_risk_pat_20260731
Revises: risk_inputs_20260731, personal_access_tokens_20260730
Create Date: 2026-06-29

After the dev pull, two un-converged heads existed:
  * ``risk_inputs_20260731``            — risk_assessments.decision_inputs column
  * ``personal_access_tokens_20260730`` — personal access tokens tables

They are on independent feature lineages with no overlapping DDL. This no-op merge
converges them so ``alembic upgrade head`` resolves to a single head again. Purely
structural — no schema or data change.
"""

from collections.abc import Sequence

revision: str = "merge_risk_pat_20260731"
down_revision: tuple[str, str] = (
    "risk_inputs_20260731",
    "personal_access_tokens_20260730",
)
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """No-op: structural merge of two existing heads."""
    pass


def downgrade() -> None:
    """No-op: splits back into the two prior heads."""
    pass

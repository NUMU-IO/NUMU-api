"""Add risk_assessments.decision_inputs (non-PII final-score input snapshot).

Revision ID: risk_inputs_20260731
Revises: merge_v3_wa_heads_20260730
Create Date: 2026-06-29

A nullable JSONB column holding the non-PII determinants of the inputs a FINAL COD
score consumed (address length, phone state, network/order/customer scalars), so the
Trust Network shadow/equivalence replay can reproduce the recorded ``risk_score``
without re-introducing raw PII. NULL on preliminary rows. Purely additive.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "risk_inputs_20260731"
down_revision: str | None = "merge_v3_wa_heads_20260730"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "risk_assessments",
        sa.Column("decision_inputs", JSONB(), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("risk_assessments", "decision_inputs", schema="public")

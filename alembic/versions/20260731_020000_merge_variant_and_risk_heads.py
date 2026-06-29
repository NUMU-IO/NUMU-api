"""Merge the variant-cents and risk/PAT migration heads.

Revision ID: merge_variant_risk_20260731
Revises: backfill_variant_cents_20260731, merge_risk_pat_20260731
Create Date: 2026-07-31

Two heads branched off ``personal_access_tokens_20260730``: the variant
price-cents backfill (``backfill_variant_cents_20260731``) and the
risk-decision-inputs / PAT merge (``merge_risk_pat_20260731``). With both
present, the CD's ``alembic upgrade head`` can't pick a single target and the
deploy fails with "Multiple head revisions are present". This is a no-DDL merge
that unifies them into one head. See memory: alembic-sibling-branch-deploy-drift
(prefer `alembic merge heads` over rebasing already-shipped siblings).
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "merge_variant_risk_20260731"
down_revision: tuple[str, str] = (
    "backfill_variant_cents_20260731",
    "merge_risk_pat_20260731",
)
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

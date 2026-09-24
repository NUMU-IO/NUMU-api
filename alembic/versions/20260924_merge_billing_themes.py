"""Merge the billing follow-up and partner themes heads.

Revision ID: merge_billing_themes_0924
Revises: billing_followup_20260924, partner_themes_20260924
Create Date: 2026-09-24
"""

from collections.abc import Sequence

revision: str = "merge_billing_themes_0924"
down_revision: str | Sequence[str] | None = (
    "billing_followup_20260924",
    "partner_themes_20260924",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

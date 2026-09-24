"""Merge the app billing v2 and partner members heads.

Revision ID: merge_appbill_members_0924
Revises: app_billing_v2_20260924, partner_members_20260924
Create Date: 2026-09-24
"""

from collections.abc import Sequence

revision: str = "merge_appbill_members_0924"
down_revision: str | Sequence[str] | None = (
    "app_billing_v2_20260924",
    "partner_members_20260924",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

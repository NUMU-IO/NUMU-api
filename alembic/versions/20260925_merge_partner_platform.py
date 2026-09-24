"""Merge the heads left by the partner-platform PRs.

Revision ID: merge_partner_platform_0925
Revises: paid_themes_20260924, merge_review_support_0924,
    partner_analytics_20260924
Create Date: 2026-09-24

The billing chain (app_billing_v2 -> billing_followup -> paid_themes) and the
review chain (review_listing + reviews_support) branched before
merge_heads_20260925 landed, so dev would otherwise end with three heads and
`alembic upgrade head` refuses to run. No schema change.
"""

from collections.abc import Sequence

revision: str = "merge_partner_platform_0925"
down_revision: str | Sequence[str] | None = (
    "paid_themes_20260924",
    "merge_review_support_0924",
    "partner_analytics_20260924",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

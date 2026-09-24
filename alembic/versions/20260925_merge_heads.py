"""Merge the four heads dev reached on 2026-09-24.

Revision ID: merge_heads_20260925
Revises: events_private_20260924, partner_themes_20260924,
    referrals_dir_20260924, ent_exempt_stores_20260925
Create Date: 2026-09-24

The partner-app PRs branched from partner_members_20260924 and the
entitlements stack from kashier_card_token_20260923; `alembic upgrade head`
refuses to run with more than one head. No schema change.
"""

from collections.abc import Sequence

revision: str = "merge_heads_20260925"
down_revision: str | Sequence[str] | None = (
    "events_private_20260924",
    "partner_themes_20260924",
    "referrals_dir_20260924",
    "ent_exempt_stores_20260925",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""Merge admin operations work with the agent and tiktok branches

Revision ID: a2801f7661b1
Revises: agent_turn_tokens_20260908, tiktok_pixel_dedup_20260908, support_cases_20260909
Create Date: 2026-09-09 02:10:37.432603

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a2801f7661b1"
down_revision: str | None = (
    "agent_turn_tokens_20260908",
    "tiktok_pixel_dedup_20260908",
    "support_cases_20260909",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

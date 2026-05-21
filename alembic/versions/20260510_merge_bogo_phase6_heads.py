"""Merge promo_targeting_limits and phase_6_platform heads.

Revision ID: merge_bogo_phase6_20260510
Revises: promo_targeting_limits_20260509, phase_6_platform_20260509
Create Date: 2026-05-10

PR #257 (BOGO targeting + usage limits) and PR #258-ish (Phase 6
platform tables: app platform / undo / multi-currency rates) landed on
``dev`` in parallel, both anchored on ``merge_heads_20260509``. That
left ``alembic upgrade head`` failing with "Multiple head revisions
are present". This is a no-op join node; both branches are independent
schema additions so there's nothing to reconcile.
"""

from collections.abc import Sequence

revision: str = "merge_bogo_phase6_20260510"
down_revision: tuple[str, str] = (
    "promo_targeting_limits_20260509",
    "phase_6_platform_20260509",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema changes — purely a graph-join."""
    pass


def downgrade() -> None:
    """No schema changes."""
    pass

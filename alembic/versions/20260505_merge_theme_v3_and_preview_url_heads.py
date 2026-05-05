"""Merge v3_theme_001 with theme_preview_url_20260503.

Revision ID: merge_theme_heads_20260505
Revises: v3_theme_001, theme_preview_url_20260503
Create Date: 2026-05-05

Two parallel theme-related migration chains landed on dev around the
same time and never had a common descendant:

  * ``v3_theme_001`` — V3 theme engine (from PR #204, feature/theme-engine-v3)
  * ``theme_preview_url_20260503`` — admin-uploadable preview override
    (landed earlier on dev)

`alembic upgrade head` failed at deploy with "Multiple head revisions are
present". This is a no-op merge node that joins both branches so a
single ``head`` exists again.
"""

from collections.abc import Sequence

revision: str = "merge_theme_heads_20260505"
down_revision: tuple[str, str] = ("v3_theme_001", "theme_preview_url_20260503")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema changes — purely a graph-join."""
    pass


def downgrade() -> None:
    """No schema changes."""
    pass

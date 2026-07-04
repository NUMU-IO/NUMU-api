"""merge template_suffix + theme_error_events + metafields siblings

Revision ID: merge_heads_20260704
Revises: metafields_foundation_20260704, add_template_suffix_20260704, theme_error_events_20260704
Create Date: 2026-07-04 06:57:56.015845

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "merge_heads_20260704"
down_revision: str | None = (
    "metafields_foundation_20260704",
    "add_template_suffix_20260704",
    "theme_error_events_20260704",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""merge ncl_dedup_key and v3_theme_zatca heads

Revision ID: merge_heads_20260607
Revises: ncl_dedup_key_20260603, merge_v3_theme_zatca_20260603
Create Date: 2026-06-07 21:30:58.488457

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "merge_heads_20260607"
down_revision: str | None = (
    "ncl_dedup_key_20260603",
    "merge_v3_theme_zatca_20260603",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""merge heads

Revision ID: baaa87d27421
Revises: b1c2d3e4f5a6, c3d4e5f6a7b8
Create Date: 2026-02-03 17:41:44.896487

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "baaa87d27421"
down_revision: str | None = ("b1c2d3e4f5a6", "c3d4e5f6a7b8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

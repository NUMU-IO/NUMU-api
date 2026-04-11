"""merge referral and theme heads

Revision ID: d759b0f72f37
Revises: f6a7b8c90d23, f1a2b3c4d5e6
Create Date: 2026-04-12 00:52:07.359757

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d759b0f72f37"
down_revision: str | None = ("f6a7b8c90d23", "f1a2b3c4d5e6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

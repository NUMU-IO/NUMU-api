"""merge tenant_lifecycle and theme_tables heads

Revision ID: f1a2b3c4d5e6
Revises: d4e5f6a70b01, a4b5c6d7e8f9
Create Date: 2026-04-11 00:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = ("d4e5f6a70b01", "a4b5c6d7e8f9")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

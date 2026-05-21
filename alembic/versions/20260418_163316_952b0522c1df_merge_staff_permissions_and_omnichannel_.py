"""merge staff_permissions and omnichannel heads

Revision ID: 952b0522c1df
Revises: roles_deleted_at_fix_001, f1a2b3c40418
Create Date: 2026-04-18 16:33:16.157607

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "952b0522c1df"
down_revision: str | None = ("roles_deleted_at_fix_001", "f1a2b3c40418")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

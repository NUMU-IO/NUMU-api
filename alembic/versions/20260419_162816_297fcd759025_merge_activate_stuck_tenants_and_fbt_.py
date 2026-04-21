"""merge activate_stuck_tenants and fbt_bundles heads

Revision ID: 297fcd759025
Revises: activate_stuck_tenants_20260419, fbt_bundles_20260419
Create Date: 2026-04-19 16:28:16.188538

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "297fcd759025"
down_revision: str | None = ("activate_stuck_tenants_20260419", "fbt_bundles_20260419")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

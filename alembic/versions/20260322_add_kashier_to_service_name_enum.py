"""Add kashier to service_name_enum.

Revision ID: 4a8ec78130c6
Revises: cc2233445566
Create Date: 2026-03-22
"""

from alembic import op

# revision identifiers
revision: str = "4a8ec78130c6"
down_revision: str | None = "cc2233445566"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE service_name_enum ADD VALUE IF NOT EXISTS 'kashier'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly.
    pass

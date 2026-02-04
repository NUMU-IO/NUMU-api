"""Add default_language column to stores table.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8f
Create Date: 2026-02-04
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if column already exists (idempotent)
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'stores' "
            "AND column_name = 'default_language'"
        )
    )
    if result.fetchone() is None:
        op.add_column(
            "stores",
            sa.Column(
                "default_language",
                sa.String(5),
                nullable=False,
                server_default="en",
            ),
            schema="public",
        )


def downgrade() -> None:
    op.drop_column("stores", "default_language", schema="public")

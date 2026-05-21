"""add google oauth fields to users

Revision ID: 239e629beae1
Revises: 1a92a389c29b
Create Date: 2026-04-02

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "239e629beae1"
down_revision: str | None = "1a92a389c29b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auth_provider", sa.String(20), nullable=True),
        schema="public",
    )
    op.add_column(
        "users",
        sa.Column("google_id", sa.String(255), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_users_google_id",
        "users",
        ["google_id"],
        unique=True,
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("ix_users_google_id", table_name="users", schema="public")
    op.drop_column("users", "google_id", schema="public")
    op.drop_column("users", "auth_provider", schema="public")

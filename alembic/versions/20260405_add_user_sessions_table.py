"""add user_sessions table for session tracking

Revision ID: 7d0e5f1a3c4b
Revises: 6c9d4e0f2b3a
Create Date: 2026-04-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "7d0e5f1a3c4b"
down_revision: str | None = "6c9d4e0f2b3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "device_name", sa.String(100), nullable=False, server_default="Unknown"
        ),
        sa.Column(
            "device_type", sa.String(20), nullable=False, server_default="desktop"
        ),
        sa.Column("browser", sa.String(50), nullable=True),
        sa.Column("os", sa.String(50), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_user_sessions_user_active",
        "user_sessions",
        ["user_id", "is_active"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_sessions_user_active",
        table_name="user_sessions",
        schema="public",
    )
    op.drop_table("user_sessions", schema="public")

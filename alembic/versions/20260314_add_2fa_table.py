"""Add two_factor_auth table.

Revision ID: ff0011223344
Revises: ee1234567890
Create Date: 2026-03-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision: str = "ff0011223344"
down_revision: str | None = "ee1234567890"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "two_factor_auth",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(20), nullable=False, server_default="totp"),
        sa.Column("status", sa.String(20), nullable=False, server_default="disabled"),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column(
            "backup_codes", ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "backup_codes_remaining", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["public.users.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name="uq_two_factor_auth_user_id"),
        schema="public",
    )
    op.create_index(
        "idx_two_factor_auth_user_id",
        "two_factor_auth",
        ["user_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_two_factor_auth_user_id", table_name="two_factor_auth", schema="public"
    )
    op.drop_table("two_factor_auth", schema="public")

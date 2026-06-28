"""Add personal_access_tokens table for machine API clients (e.g. MCP server).

Revision ID: personal_access_tokens_20260730
Revises: store_theme_files_20260627
Create Date: 2026-07-30

Creates the ``public.personal_access_tokens`` table backing long-lived,
hashed API tokens. A token is scoped to a (user_id, tenant_id) pair and
inherits that membership's permissions, so the existing RBAC / plan-limit
checks keep applying. Only the SHA-256 hash of each token is stored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "personal_access_tokens_20260730"
down_revision: str = "store_theme_files_20260627"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personal_access_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_prefix", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "ix_personal_access_tokens_token_hash",
        "personal_access_tokens",
        ["token_hash"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_personal_access_tokens_user_id",
        "personal_access_tokens",
        ["user_id"],
        schema="public",
    )
    op.create_index(
        "ix_personal_access_tokens_tenant_id",
        "personal_access_tokens",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_personal_access_tokens_store_id",
        "personal_access_tokens",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_personal_access_tokens_user_tenant",
        "personal_access_tokens",
        ["user_id", "tenant_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_personal_access_tokens_user_tenant",
        table_name="personal_access_tokens",
        schema="public",
    )
    op.drop_index(
        "ix_personal_access_tokens_store_id",
        table_name="personal_access_tokens",
        schema="public",
    )
    op.drop_index(
        "ix_personal_access_tokens_tenant_id",
        table_name="personal_access_tokens",
        schema="public",
    )
    op.drop_index(
        "ix_personal_access_tokens_user_id",
        table_name="personal_access_tokens",
        schema="public",
    )
    op.drop_index(
        "ix_personal_access_tokens_token_hash",
        table_name="personal_access_tokens",
        schema="public",
    )
    op.drop_table("personal_access_tokens", schema="public")

"""Add social_connections table for social media import feature.

Revision ID: ee7788990011
Revises: dd6677889900
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ENUM

from alembic import op

# revision identifiers
revision = "ee7788990011"
down_revision = "dd6677889900"

branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types (IF NOT EXISTS for idempotency)
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE public.socialplatform AS ENUM ('instagram', 'facebook'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE public.socialconnectionstatus AS ENUM ('active', 'disconnected'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    op.create_table(
        "social_connections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "platform",
            ENUM(
                "instagram",
                "facebook",
                name="socialplatform",
                schema="public",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("platform_account_id", sa.String(255), nullable=False),
        sa.Column("handle", sa.String(255), nullable=False),
        sa.Column("followers", sa.Integer, nullable=False, server_default="0"),
        sa.Column("posts_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("access_token_encrypted", sa.Text, nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            ENUM(
                "active",
                "disconnected",
                name="socialconnectionstatus",
                schema="public",
                create_type=False,
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("social_connections", schema="public")
    op.execute("DROP TYPE IF EXISTS public.socialconnectionstatus")
    op.execute("DROP TYPE IF EXISTS public.socialplatform")

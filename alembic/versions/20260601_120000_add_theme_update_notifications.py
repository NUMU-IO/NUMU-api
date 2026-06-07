"""Add marketplace_theme_update_notifications table (Phase 5.1).

Additive: new ``public.marketplace_theme_update_notifications`` table only. No
changes to existing tables, so it's safe for production stores — they simply
have no notification rows until a version bump is detected. NOT auto-applied
to prod; run explicitly.

Revision ID: add_theme_update_notifs_20260601
Revises: add_pages_20260601
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "add_theme_update_notifs_20260601"
down_revision: str | None = "add_pages_20260601"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "marketplace_theme_update_notifications"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("theme_id", UUID(as_uuid=True), nullable=False),
        sa.Column("from_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("to_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "from_version", sa.String(length=50), nullable=False, server_default=""
        ),
        sa.Column(
            "to_version", sa.String(length=50), nullable=False, server_default=""
        ),
        sa.Column(
            "classification",
            sa.String(length=16),
            nullable=False,
            server_default="automatic",
        ),
        sa.Column(
            "changes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("release_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
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
        sa.UniqueConstraint(
            "store_id", "to_version_id", name="uq_theme_update_store_version"
        ),
        schema="public",
    )
    op.create_index("ix_tun_tenant_id", _TABLE, ["tenant_id"], schema="public")
    op.create_index("ix_tun_store_id", _TABLE, ["store_id"], schema="public")
    op.create_index("ix_tun_status", _TABLE, ["status"], schema="public")


def downgrade() -> None:
    op.drop_index("ix_tun_status", table_name=_TABLE, schema="public")
    op.drop_index("ix_tun_store_id", table_name=_TABLE, schema="public")
    op.drop_index("ix_tun_tenant_id", table_name=_TABLE, schema="public")
    op.drop_table(_TABLE, schema="public")

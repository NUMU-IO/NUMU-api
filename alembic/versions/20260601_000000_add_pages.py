"""Add pages table for merchant content pages (Phase 4.4b).

Additive: new ``public.pages`` table only. No changes to existing tables,
so it's safe for production stores — they simply have no page rows until
the merchant creates them. NOT auto-applied to prod; run explicitly.

Revision ID: add_pages_20260601
Revises: add_menus_20260531
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "add_pages_20260601"
down_revision: str | None = "add_menus_20260531"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pages",
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
        sa.Column("handle", sa.String(length=255), nullable=False),
        sa.Column(
            "title", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "body", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "seo", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "is_published", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "template", sa.String(length=64), nullable=False, server_default="page"
        ),
        sa.Column(
            "content_v3", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
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
        sa.UniqueConstraint("store_id", "handle", name="uq_pages_store_handle"),
        schema="public",
    )
    op.create_index("ix_pages_tenant_id", "pages", ["tenant_id"], schema="public")
    op.create_index("ix_pages_store_id", "pages", ["store_id"], schema="public")
    op.create_index("ix_pages_handle", "pages", ["handle"], schema="public")


def downgrade() -> None:
    op.drop_index("ix_pages_handle", table_name="pages", schema="public")
    op.drop_index("ix_pages_store_id", table_name="pages", schema="public")
    op.drop_index("ix_pages_tenant_id", table_name="pages", schema="public")
    op.drop_table("pages", schema="public")

"""Add menus table for store navigation / link lists (Phase 2.1).

Additive: new ``public.menus`` table only. No changes to existing tables
(store_themes etc.), so it's safe for production stores — they simply have
no menu rows until seeded.

Revision ID: add_menus_20260531
Revises: merge_v3_wa_heads_20260730
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "add_menus_20260531"
down_revision: str | None = "merge_v3_wa_heads_20260730"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "menus",
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
            "items", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
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
        sa.UniqueConstraint("store_id", "handle", name="uq_menus_store_handle"),
        schema="public",
    )
    op.create_index("ix_menus_tenant_id", "menus", ["tenant_id"], schema="public")
    op.create_index("ix_menus_store_id", "menus", ["store_id"], schema="public")
    op.create_index("ix_menus_handle", "menus", ["handle"], schema="public")


def downgrade() -> None:
    op.drop_index("ix_menus_handle", table_name="menus", schema="public")
    op.drop_index("ix_menus_store_id", table_name="menus", schema="public")
    op.drop_index("ix_menus_tenant_id", table_name="menus", schema="public")
    op.drop_table("menus", schema="public")

"""Add store_theme_files table (in-app theme code editor workspace).

Revision ID: store_theme_files_20260627
Revises: store_theme_name_20260627
Create Date: 2026-06-27

Backs the Online Store code editor. Each row is one editable source file of a
store's theme workspace; the full row set is a complete, buildable theme
project. On Publish the rows are materialized to disk and fed through the
existing external-theme build pipeline (the file store replaces the git clone
as the build source).

Tenant-scoped but, like ``store_themes``, relies on app-level scoping
(``verify_store_ownership`` + store_id-filtered queries) rather than a DB RLS
policy — no CREATE POLICY here, matching the sibling theme tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "store_theme_files_20260627"
down_revision: str = "store_theme_name_20260627"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "store_theme_files",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["store_id"], ["public.stores.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("store_id", "path", name="uq_store_theme_files_store_path"),
        schema="public",
    )
    op.create_index(
        "ix_store_theme_files_store_id",
        "store_theme_files",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_store_theme_files_tenant_id",
        "store_theme_files",
        ["tenant_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_store_theme_files_tenant_id",
        table_name="store_theme_files",
        schema="public",
    )
    op.drop_index(
        "ix_store_theme_files_store_id",
        table_name="store_theme_files",
        schema="public",
    )
    op.drop_table("store_theme_files", schema="public")

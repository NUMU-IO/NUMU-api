"""add_page_views_table

Revision ID: a1b2c3d4e5f6
Revises: 0c1c1d87f317
Create Date: 2026-04-01 17:09:50.523182

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "0c1c1d87f317"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "page_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("session_fingerprint", sa.String(64), nullable=True, index=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("referrer", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "ix_page_views_store_created",
        "page_views",
        ["store_id", "created_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_page_views_store_created",
        table_name="page_views",
        schema="public",
    )
    op.drop_table("page_views", schema="public")

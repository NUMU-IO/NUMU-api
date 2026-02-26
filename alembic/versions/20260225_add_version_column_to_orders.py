"""Add version column to orders table for optimistic locking.

Revision ID: c3d4e5f6a7b8
Revises: b2d3e4f5a6c7
Create Date: 2026-02-25

Adds:
- public.orders.version — integer column for optimistic locking (default 1)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2d3e4f5a6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("orders", "version", schema="public")

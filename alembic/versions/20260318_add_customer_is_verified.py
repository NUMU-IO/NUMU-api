"""Add is_verified column to public.customers table.

Revision ID: cc2233445566
Revises: bb1122334455
Create Date: 2026-03-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "cc2233445566"
down_revision: str | None = "bb1122334455"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='customers' "
            "AND column_name='is_verified'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "customers",
            sa.Column(
                "is_verified", sa.Boolean(), nullable=False, server_default="false"
            ),
            schema="public",
        )


def downgrade() -> None:
    op.drop_column("customers", "is_verified", schema="public")

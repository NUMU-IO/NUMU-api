"""Add password_hash column to public.customers table.

Revision ID: bb1122334455
Revises: aa9988776655
Create Date: 2026-03-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bb1122334455"
down_revision: str | None = "aa9988776655"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("password_hash", sa.String(255), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("customers", "password_hash", schema="public")

"""Add payment_failed value to orderstatus enum.

Revision ID: 8f7e4d3c2b1a
Revises: 7e6d3c9b5a2f
Create Date: 2026-02-20

The Python OrderStatus enum already has PAYMENT_FAILED but the Postgres
enum type was never updated to include it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8f7e4d3c2b1a"
down_revision: str | None = "7e6d3c9b5a2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The StrEnum stores lowercase values (e.g. "pending", "confirmed").
    op.execute("ALTER TYPE public.orderstatus ADD VALUE IF NOT EXISTS 'payment_failed'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    pass

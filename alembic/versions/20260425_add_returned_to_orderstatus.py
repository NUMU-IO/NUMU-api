"""Add returned value to orderstatus enum.

Revision ID: returned_orderstatus_20260425
Revises: bundle_reason_20260425
Create Date: 2026-04-25

The Python OrderStatus enum gains a new RETURNED value so manual-ship
merchants (no Bosta integration) can record an RTO outcome that feeds
the cross-merchant trust network. Postgres enum types need an explicit
ALTER TYPE to accept new values.

Mirrors the pattern in 20260220_add_payment_failed_to_orderstatus.py —
SQLAlchemy stores the lowercase StrEnum value, so we add 'returned'
(lowercase) to the enum type.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "returned_orderstatus_20260425"
down_revision: str | None = "bundle_reason_20260425"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE public.orderstatus ADD VALUE IF NOT EXISTS 'returned'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    pass

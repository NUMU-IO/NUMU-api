"""Add business_hours JSONB column to stores.

Per-store, theme-agnostic business hours. Shape (validated only at the
schema layer — JSONB stays a dict):

    {
      "timezone": "Africa/Cairo",
      "days": {
        "mon": {"open": "09:00", "close": "22:00", "closed": false},
        ...
      }
    }

Revision ID: store_business_hours_20260425
Revises: bundle_reason_20260425
Create Date: 2026-04-25 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "store_business_hours_20260425"
down_revision: str | None = "bundle_reason_20260425"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # Idempotent — safe to re-run on environments where a manual hotfix
    # already added the column.
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'stores' "
            "AND column_name = 'business_hours'"
        )
    )
    if result.fetchone() is None:
        op.add_column(
            "stores",
            sa.Column("business_hours", JSONB, nullable=True),
            schema="public",
        )


def downgrade() -> None:
    op.drop_column("stores", "business_hours", schema="public")

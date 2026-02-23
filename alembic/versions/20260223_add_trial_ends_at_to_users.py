"""Add trial_ends_at to users table.

Revision ID: b2d3e4f5a6c7
Revises: a1c2e3f4d5b6
Create Date: 2026-02-23

Adds:
- public.users.trial_ends_at — nullable timestamp for 14-day trial period
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2d3e4f5a6c7"
down_revision: str | None = "a1c2e3f4d5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("users", "trial_ends_at", schema="public")

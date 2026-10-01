"""Remember the merchant's language on the user.

Revision ID: users_language_20261001
Revises: deposit_amount_actual_20260930
Create Date: 2026-10-01

Signup sent the landing page's language but nothing kept it, so the hub fell
back to the device language (English on most Egyptian phones) and auth emails
were always Arabic. NULL means "not chosen yet"; readers fall back to Arabic.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "users_language_20261001"
down_revision: str | Sequence[str] | None = "deposit_amount_actual_20260930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("language", sa.String(length=2), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("users", "language", schema="public")

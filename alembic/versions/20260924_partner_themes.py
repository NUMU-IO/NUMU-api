"""Partner themes: Arabic listing name and description.

Revision ID: partner_themes_20260924
Revises: partner_members_20260924
Create Date: 2026-09-24

Adds ``name_ar`` and ``description_ar`` to ``public.marketplace_themes`` so a
partner can list a theme in both languages. Both nullable, no backfill.

Idempotent (IF NOT EXISTS). Downgrade drops both columns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "partner_themes_20260924"
down_revision: str | Sequence[str] | None = "partner_members_20260924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.marketplace_themes "
        "ADD COLUMN IF NOT EXISTS name_ar VARCHAR(255), "
        "ADD COLUMN IF NOT EXISTS description_ar TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.marketplace_themes "
        "DROP COLUMN IF EXISTS description_ar, "
        "DROP COLUMN IF EXISTS name_ar"
    )

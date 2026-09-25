"""apps.draft_manifest: the partner portal's section-by-section app editor.

Revision ID: app_draft_manifest_20260925
Revises: public_api_limits_20260925
Create Date: 2026-09-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "app_draft_manifest_20260925"
down_revision: str | None = "public_api_limits_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.apps ADD COLUMN IF NOT EXISTS draft_manifest jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE public.apps DROP COLUMN IF EXISTS draft_manifest")

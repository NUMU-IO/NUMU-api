"""merge heads

Revision ID: 3717ff7cf723
Revises: celery_dead_letters_20260508, platform_source_20260511, marketing_campaigns_20260722
Create Date: 2026-05-11 18:07:41.007591

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "3717ff7cf723"
down_revision: str | None = (
    "celery_dead_letters_20260508",
    "platform_source_20260511",
    "marketing_campaigns_20260722",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""merge reviews support and review listing

Revision ID: merge_review_support_0924
Revises: reviews_support_20260924, review_listing_20260924
Create Date: 2026-09-24 20:57:45.983959

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "merge_review_support_0924"
down_revision: str | None = ("reviews_support_20260924", "review_listing_20260924")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

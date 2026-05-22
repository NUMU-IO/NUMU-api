"""merge post-attribution stacks

Revision ID: post_attr_merge_20260522
Revises: short_links_20260522, campaign_coupon_fk_20260522, customer_touches_20260522
Create Date: 2026-05-22 19:24:18.387697

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "post_attr_merge_20260522"
down_revision: str | None = (
    "short_links_20260522",
    "campaign_coupon_fk_20260522",
    "customer_touches_20260522",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

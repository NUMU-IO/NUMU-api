"""Merge the thread-customer-link and ttclid heads on dev.

Revision ID: merge_thread_ttclid_20260822
Revises: thread_customer_20260821, touch_ttclid_20260821
Create Date: 2026-08-22

Both 2026-08-21 migrations branched off ``chconn_linked_page_20260821``.
No-op structural merge so ``alembic upgrade head`` resolves to one head.
"""

from collections.abc import Sequence

revision: str = "merge_thread_ttclid_20260822"
down_revision: tuple[str, str] = (
    "thread_customer_20260821",
    "touch_ttclid_20260821",
)
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """No-op: structural merge of two existing heads."""
    pass


def downgrade() -> None:
    """No-op: splits back into the two prior heads."""
    pass

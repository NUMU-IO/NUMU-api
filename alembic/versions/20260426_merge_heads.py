"""Merge two migration heads that diverged at bundle_reason_20260425.

Revision ID: merge_heads_20260426
Revises: pending_dep_upper_20260426, email_templates_20260426
Create Date: 2026-04-26

After ``bundle_reason_20260425`` two parallel branches were committed:

    bundle_reason
      ├─ returned_orderstatus → returned_upper → pending_dep_upper
      └─ store_business_hours → email_templates

Alembic refuses ``upgrade head`` while two heads exist. This is a pure
merge — no DDL, no logic — that picks the linear successor chain so a
plain ``upgrade head`` works again.
"""

from collections.abc import Sequence

revision: str = "merge_heads_20260426"
down_revision: tuple[str, str] = (
    "pending_dep_upper_20260426",
    "email_templates_20260426",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

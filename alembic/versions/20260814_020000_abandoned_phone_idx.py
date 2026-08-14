"""Point-lookup index for abandoned-checkout phone matching.

``find_active_for_session`` now matches on phone (the identity layer
captures it long before email exists), and both the cart-track upsert and
order-time reconciliation run it on hot paths. The sweep index from
checkout_identity_20260814 is shaped for range scans over
``last_activity_at`` and cannot serve a ``(store_id, phone)`` point
lookup, so this partial b-tree does. Partial because most rows are
contactless — the index stays tiny.

Idempotent DDL, same lesson as the parent revision: converge on the
intended state regardless of what already exists.

Revision ID: abandoned_phone_idx_20260814
Revises: checkout_identity_20260814
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "abandoned_phone_idx_20260814"
down_revision: str | Sequence[str] | None = "checkout_identity_20260814"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_abandoned_checkouts_store_phone",
        "abandoned_checkouts",
        ["store_id", "phone"],
        postgresql_where=sa.text("phone IS NOT NULL"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_abandoned_checkouts_store_phone",
        table_name="abandoned_checkouts",
        if_exists=True,
    )

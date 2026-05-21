"""Add session_fingerprint to orders table.

Revision ID: c1d2e3f40901
Revises: 8e1f6a2b4d5c
Create Date: 2026-04-09

Stores the storefront session fingerprint on the order so payment webhooks
(paymob, kashier) can read it back when emitting the `order_completed`
funnel event. The funnel COUNT(DISTINCT session_fingerprint) query needs
the same fingerprint as the visitor's earlier page_view / add_to_cart /
checkout_started events to dedupe correctly.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "c1d2e3f40901"
down_revision: str | None = "8e1f6a2b4d5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("session_fingerprint", sa.String(64), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("orders", "session_fingerprint", schema="public")

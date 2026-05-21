"""add store_id to payment_reconciliation_runs

Revision ID: 6c9d4e0f2b3a
Revises: 5b8c3d9e0f1a
Create Date: 2026-04-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "6c9d4e0f2b3a"
down_revision: str | None = "5b8c3d9e0f1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_reconciliation_runs",
        sa.Column("store_id", UUID(as_uuid=True), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_recon_runs_store_id",
        "payment_reconciliation_runs",
        ["store_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recon_runs_store_id",
        table_name="payment_reconciliation_runs",
        schema="public",
    )
    op.drop_column(
        "payment_reconciliation_runs",
        "store_id",
        schema="public",
    )

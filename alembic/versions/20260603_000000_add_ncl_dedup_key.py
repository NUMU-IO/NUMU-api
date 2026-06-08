"""Add dedup_key to network_contribution_log for idempotent network writes (P1-2).

A unique key over non-null values makes a keyed ``write_network_event`` truly
idempotent at the DB level: a second write with the same (order, event_type)
key conflicts and is skipped, so a courier webhook and the nightly
reconciliation sweep can never double-count the same outcome. Legacy rows keep
``dedup_key = NULL`` (Postgres allows many NULLs under a unique index), so the
old append-only behaviour is unchanged for callers that pass no key.

Revision ID: ncl_dedup_key_20260603
Revises: zatca_invoice_20260602
Create Date: 2026-06-03
"""

import sqlalchemy as sa

from alembic import op

revision = "ncl_dedup_key_20260603"
down_revision = "zatca_invoice_20260602"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "network_contribution_log",
        sa.Column("dedup_key", sa.String(length=128), nullable=True),
        schema="public",
    )
    op.create_index(
        "uq_ncl_dedup_key",
        "network_contribution_log",
        ["dedup_key"],
        unique=True,
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_ncl_dedup_key",
        table_name="network_contribution_log",
        schema="public",
    )
    op.drop_column("network_contribution_log", "dedup_key", schema="public")

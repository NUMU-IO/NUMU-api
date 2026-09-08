"""Support cases — the platform's own ticket record.

Revision ID: support_cases_20260909
Revises: payment_failed_upper_20260909
Create Date: 2026-09-09

The admin has always shown an "open support cases" number with nothing behind
it. Support currently lives in WhatsApp threads and inboxes, so the count of
what staff actually owe a merchant is not written down anywhere, and the one
place it is asked for — the operations overview — had to render a dash.

This is the smallest table that makes that number real: who raised it, which
merchant it concerns, what it is about, how urgent, who owns it, and whether
it is still open. Deliberately not a full helpdesk — no threading, no SLA
engine, no canned replies. Those are a product decision; a case having an
owner and a status is a bookkeeping one.

`entity_type`/`entity_id` are a loose reference rather than a foreign key: a
case is often about an order that has since been deleted, or about nothing in
particular, and a hard FK would either block the delete or lose the case.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "support_cases_20260909"
down_revision: str | None = "payment_failed_upper_20260909"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_cases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Nullable: a case can be raised before anyone knows which merchant it
        # belongs to, which is exactly when triage matters most.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        # open → pending_merchant → resolved → closed. Kept as text with a
        # check rather than an enum: this vocabulary will change, and adding a
        # value to a Postgres enum has already cost this codebase two
        # migrations (see 20260426_*_uppercase_fix.py).
        sa.Column(
            "status", sa.String(length=24), nullable=False, server_default="open"
        ),
        sa.Column(
            "priority", sa.String(length=16), nullable=False, server_default="normal"
        ),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("reporter_email", sa.String(length=255), nullable=True),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('open','pending_merchant','resolved','closed')",
            name="ck_support_cases_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low','normal','high','urgent')",
            name="ck_support_cases_priority",
        ),
        schema="public",
    )
    # The queue is read as "what is still open, oldest first", so index for
    # exactly that rather than on status alone.
    op.create_index(
        "ix_support_cases_status_created",
        "support_cases",
        ["status", "created_at"],
        schema="public",
    )
    op.create_index(
        "ix_support_cases_tenant",
        "support_cases",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_support_cases_assignee",
        "support_cases",
        ["assignee_user_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_support_cases_assignee", table_name="support_cases", schema="public"
    )
    op.drop_index(
        "ix_support_cases_tenant", table_name="support_cases", schema="public"
    )
    op.drop_index(
        "ix_support_cases_status_created", table_name="support_cases", schema="public"
    )
    op.drop_table("support_cases", schema="public")

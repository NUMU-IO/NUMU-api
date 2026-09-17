"""WhatsApp access becomes a paid subscription, not just an approval.

Revision ID: wa_paid_access_20260916
Revises: wa_tmpl_backfill_20260916
Create Date: 2026-09-16

An admin approving a request used to hand the channel over for free. Now the
approval is priced: the admin sets an amount, the merchant pays and uploads the
receipt through the existing InstaPay subscription-proof flow, and verification
switches the channel on until ``active_until``.

Additive only — the new columns are nullable and the two new enum labels are
additions. A store already APPROVED keeps working: a NULL ``active_until``
means "granted, no expiry", which is what those rows are.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "wa_paid_access_20260916"
down_revision: str | Sequence[str] | None = "wa_tmpl_backfill_20260916"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enum labels first: a row cannot be written into a state the type does not
    # know. ADD VALUE is not transactional on older servers, hence IF NOT
    # EXISTS + its own autocommit block.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE public.whatsappaccessstatus "
            "ADD VALUE IF NOT EXISTS 'AWAITING_PAYMENT'"
        )
        op.execute(
            "ALTER TYPE public.whatsappaccessstatus ADD VALUE IF NOT EXISTS 'EXPIRED'"
        )

    op.add_column(
        "whatsapp_access_requests",
        sa.Column("plan_key", sa.String(32), nullable=True),
        schema="public",
    )
    op.add_column(
        "whatsapp_access_requests",
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "whatsapp_access_requests",
        sa.Column("currency", sa.String(3), nullable=True),
        schema="public",
    )
    op.add_column(
        "whatsapp_access_requests",
        sa.Column("billing_cycle", sa.String(10), nullable=True),
        schema="public",
    )
    op.add_column(
        "whatsapp_access_requests",
        sa.Column("payment_intent_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "whatsapp_access_requests",
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "whatsapp_access_requests",
        sa.Column("message_allowance", sa.Integer(), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_whatsapp_access_active_until",
        "whatsapp_access_requests",
        ["active_until"],
        schema="public",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_access_active_until",
        table_name="whatsapp_access_requests",
        schema="public",
        if_exists=True,
    )
    for column in (
        "message_allowance",
        "active_until",
        "payment_intent_id",
        "billing_cycle",
        "currency",
        "amount_cents",
        "plan_key",
    ):
        op.drop_column("whatsapp_access_requests", column, schema="public")
    # The enum labels stay. Removing a label from a PostgreSQL enum means
    # rewriting the type, and nothing reads a label it does not recognise.

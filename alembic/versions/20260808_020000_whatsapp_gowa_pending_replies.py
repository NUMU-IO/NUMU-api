"""whatsapp_gowa_pending_replies: numbered-reply correlation for GOWA

Restores what Meta's ``button.payload`` carries. whatsmeow cannot send
interactive buttons, so a prompt comes back as the bare text "1"; this table
records which payload each digit meant for a given recipient, so the inbound
webhook can hand the existing COD handlers byte-identical input.

Additive only; written and read solely by the GOWA transport.

Revision ID: wa_gowa_reply_20260808
Revises: wa_gowa_dev_20260808
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "wa_gowa_reply_20260808"
down_revision = "wa_gowa_dev_20260808"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_gowa_pending_replies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("payloads", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
        schema="public",
    )
    # The inbound lookup runs on every reply from a GOWA store.
    op.create_index(
        "ix_wa_gowa_pending_phone_live",
        "whatsapp_gowa_pending_replies",
        ["phone", "expires_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wa_gowa_pending_phone_live",
        table_name="whatsapp_gowa_pending_replies",
        schema="public",
    )
    op.drop_table("whatsapp_gowa_pending_replies", schema="public")

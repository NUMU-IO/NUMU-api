"""Add missing updated_at to channel_messages.

The omnichannel_inbox migration (20260416_000001) gave every table a
created_at/updated_at pair EXCEPT channel_messages, while
ChannelMessageModel carries TimestampMixin — so the first real inbound
message insert failed on prod with UndefinedColumnError.

Idempotent: ADD COLUMN IF NOT EXISTS, safe to re-run.

Revision ID: chmsg_updated_at_20260821
Revises: manual_pay_method_20260820
Create Date: 2026-08-21
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "chmsg_updated_at_20260821"
down_revision = "manual_pay_method_20260820"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.channel_messages
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.channel_messages DROP COLUMN IF EXISTS updated_at")

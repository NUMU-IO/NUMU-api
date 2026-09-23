"""Add tenants.kashier_card_token_encrypted for Kashier plan renewals.

Revision ID: kashier_card_token_20260923
Revises: awaiting_payment_20260923
Create Date: 2026-09-23

A plan paid on NUMU's card page can save the card with a Kashier recurring
agreement; process_due_renewals charges it each cycle. Same encryption
envelope as paymob_card_token_encrypted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "kashier_card_token_20260923"
down_revision: str | None = "awaiting_payment_20260923"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.tenants "
        "ADD COLUMN IF NOT EXISTS kashier_card_token_encrypted TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.tenants DROP COLUMN IF EXISTS kashier_card_token_encrypted"
    )

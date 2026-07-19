"""Wallet v2 — gateway-agnostic top-ups + on-hold (pending) credits.

Card top-ups move from Paymob to Kashier and Vodafone Cash becomes a
MANUAL method (merchant transfers to NUMU's own VC number and uploads a
receipt — same pipeline as InstaPay, NOT a gateway). The paymob_*-named
intent columns are renamed to gateway-agnostic names while the tables
are still unreleased (feature flags off, no rows on any environment):

- wallet_topup_intents.paymob_intention_id   -> gateway_reference
- wallet_topup_intents.paymob_client_secret  -> gateway_payload
- wallet_topup_intents.paymob_transaction_id -> gateway_transaction_id
- wallet_topup_intents.display_ipa           -> display_destination
  (IPA for InstaPay, wallet number for Vodafone Cash)

Optimistic-credit UX: soft-blocked manual receipts now credit the wallet
as ON HOLD — visible to the merchant immediately, spendable only after
verification. The hold total is the new
``merchant_wallets.pending_balance_cents`` (settled money stays in the
append-only ledger; holds are provisional and never ledger rows).

Revision ID: merchant_wallet_v2_20260719
Revises: merchant_wallet_20260719
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "merchant_wallet_v2_20260719"
down_revision: str | Sequence[str] | None = "merchant_wallet_20260719"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "merchant_wallets",
        sa.Column(
            "pending_balance_cents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="public",
    )

    op.alter_column(
        "wallet_topup_intents",
        "paymob_intention_id",
        new_column_name="gateway_reference",
        schema="public",
    )
    op.alter_column(
        "wallet_topup_intents",
        "paymob_client_secret",
        new_column_name="gateway_payload",
        schema="public",
    )
    op.alter_column(
        "wallet_topup_intents",
        "paymob_transaction_id",
        new_column_name="gateway_transaction_id",
        schema="public",
    )
    op.alter_column(
        "wallet_topup_intents",
        "display_ipa",
        new_column_name="display_destination",
        schema="public",
    )
    op.execute(
        "ALTER TABLE public.wallet_topup_intents "
        "RENAME CONSTRAINT uq_wallet_topup_intents_paymob_tx "
        "TO uq_wallet_topup_intents_gateway_tx"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.wallet_topup_intents "
        "RENAME CONSTRAINT uq_wallet_topup_intents_gateway_tx "
        "TO uq_wallet_topup_intents_paymob_tx"
    )
    op.alter_column(
        "wallet_topup_intents",
        "display_destination",
        new_column_name="display_ipa",
        schema="public",
    )
    op.alter_column(
        "wallet_topup_intents",
        "gateway_transaction_id",
        new_column_name="paymob_transaction_id",
        schema="public",
    )
    op.alter_column(
        "wallet_topup_intents",
        "gateway_payload",
        new_column_name="paymob_client_secret",
        schema="public",
    )
    op.alter_column(
        "wallet_topup_intents",
        "gateway_reference",
        new_column_name="paymob_intention_id",
        schema="public",
    )
    op.drop_column("merchant_wallets", "pending_balance_cents", schema="public")

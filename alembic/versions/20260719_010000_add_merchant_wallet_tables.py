"""Add merchant wallet tables (pay-as-you-go commission tier).

Four platform-level tables (public schema, explicit tenant FK — billing
pattern, no RLS, mirroring 20260411_add_billing_tables.py):

- ``merchant_wallets`` — one prepaid balance per tenant (denormalized
  ``balance_cents``; the ledger is authoritative).
- ``wallet_transactions`` — append-only ledger. Partial unique indexes on
  (order_id) per commission/reversal kind + unique ``idempotency_key``
  are the idempotency backbone for duplicate event delivery, webhook
  replay, and reconciliation races.
- ``wallet_topup_intents`` — one row per top-up attempt (Paymob card /
  mobile wallet / InstaPay).
- ``wallet_topup_proofs`` — InstaPay receipt uploads, tenant-scoped dedup
  (parallel to ``payment_proofs``, which is order/store-shaped).

Purely additive: no existing table is touched.

Revision ID: merchant_wallet_20260719
Revises: merge_codap_mtv_20260718
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision: str = "merchant_wallet_20260719"
down_revision: str | Sequence[str] | None = "merge_codap_mtv_20260718"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchant_wallets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("balance_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("negative_allowance_cents", sa.Integer(), nullable=True),
        sa.Column("commission_bps_override", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "last_warning_level", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="public",
    )

    op.create_table(
        "wallet_topup_intents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("special_reference", sa.String(48), nullable=False),
        sa.Column("paymob_intention_id", sa.String(255), nullable=True),
        sa.Column("paymob_client_secret", sa.Text(), nullable=True),
        sa.Column("paymob_transaction_id", sa.String(255), nullable=True),
        sa.Column("display_ipa", sa.String(80), nullable=True),
        sa.Column("qr_payload", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # FK added after wallet_transactions exists (circular reference).
        sa.Column("credited_transaction_id", UUID(as_uuid=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "special_reference", name="uq_wallet_topup_intents_reference"
        ),
        sa.UniqueConstraint(
            "paymob_transaction_id", name="uq_wallet_topup_intents_paymob_tx"
        ),
        schema="public",
    )
    op.create_index(
        "ix_wallet_topup_intents_status_expires",
        "wallet_topup_intents",
        ["status", "expires_at"],
        schema="public",
    )
    op.create_index(
        "ix_wallet_topup_intents_tenant_created",
        "wallet_topup_intents",
        ["tenant_id", "created_at"],
        schema="public",
    )

    op.create_table(
        "wallet_transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "wallet_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.merchant_wallets.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("balance_after_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("order_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "topup_intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.wallet_topup_intents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("meta", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_wallet_tx_idempotency_key"),
        schema="public",
    )
    op.create_index(
        "ix_wallet_transactions_wallet_created",
        "wallet_transactions",
        ["wallet_id", "created_at"],
        schema="public",
    )
    op.create_index(
        "uq_wallet_tx_commission_order",
        "wallet_transactions",
        ["order_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("kind = 'commission'"),
    )
    op.create_index(
        "uq_wallet_tx_reversal_order",
        "wallet_transactions",
        ["order_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("kind = 'commission_reversal'"),
    )

    op.create_foreign_key(
        "fk_wallet_topup_intents_credited_tx",
        "wallet_topup_intents",
        "wallet_transactions",
        ["credited_transaction_id"],
        ["id"],
        source_schema="public",
        referent_schema="public",
        ondelete="SET NULL",
    )

    op.create_table(
        "wallet_topup_proofs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "topup_intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.wallet_topup_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proof_image_key", sa.Text(), nullable=False),
        sa.Column("proof_image_hash", sa.LargeBinary(), nullable=False),
        sa.Column("perceptual_hash", sa.BigInteger(), nullable=True),
        sa.Column("transaction_ref", sa.String(64), nullable=False),
        sa.Column("declared_amount_cents", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="awaiting_review"
        ),
        sa.Column(
            "review_decision_by",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("review_decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("ocr_status", sa.Text(), nullable=True),
        sa.Column("ocr_extracted_amount_cents", sa.Integer(), nullable=True),
        sa.Column("ocr_extracted_ipa", sa.String(80), nullable=True),
        sa.Column("ocr_extracted_note", sa.Text(), nullable=True),
        sa.Column("ocr_extracted_transaction_ref", sa.String(64), nullable=True),
        sa.Column("ocr_extracted_recipient_name", sa.Text(), nullable=True),
        sa.Column("ocr_raw_text", sa.Text(), nullable=True),
        sa.Column("ocr_provider", sa.String(40), nullable=True),
        sa.Column("ocr_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_approval_block_reasons", ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "proof_image_hash",
            name="uq_wallet_topup_proofs_tenant_image_hash",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "transaction_ref",
            name="uq_wallet_topup_proofs_tenant_transaction_ref",
        ),
        schema="public",
    )
    op.create_index(
        "ix_wallet_topup_proofs_intent",
        "wallet_topup_proofs",
        ["topup_intent_id"],
        schema="public",
    )
    op.create_index(
        "ix_wallet_topup_proofs_status",
        "wallet_topup_proofs",
        ["status"],
        schema="public",
    )
    op.create_index(
        "ix_wallet_topup_proofs_tenant_phash",
        "wallet_topup_proofs",
        ["tenant_id", "perceptual_hash"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("wallet_topup_proofs", schema="public")
    op.drop_constraint(
        "fk_wallet_topup_intents_credited_tx",
        "wallet_topup_intents",
        schema="public",
        type_="foreignkey",
    )
    op.drop_table("wallet_transactions", schema="public")
    op.drop_table("wallet_topup_intents", schema="public")
    op.drop_table("merchant_wallets", schema="public")

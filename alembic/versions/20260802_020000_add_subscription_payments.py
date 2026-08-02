"""Add subscription payment tables (InstaPay plan payments).

Two platform-level tables (public schema, explicit tenant FK — billing
pattern, mirroring 20260719_010000_add_merchant_wallet_tables.py):

- ``subscription_payment_intents`` — one row per "merchant wants to pay
  for a plan via InstaPay" attempt. Amount is a server-side snapshot of
  the plan price at creation; the merchant never chooses it.
- ``subscription_payment_proofs`` — uploaded transfer receipts,
  tenant-scoped dedup + OCR enrichment (parallel to
  ``wallet_topup_proofs``; the reusable pipeline pieces are functions,
  not the row shape).

Plus one additive column: ``billing_invoices.subscription_payment_intent_id``
links a paid invoice back to the InstaPay intent that funded it (the
manual-payment sibling of ``paymob_transaction_id``).

Purely additive: no existing rows are touched.

Revision ID: sub_instapay_20260802
Revises: abandoned_fp_idx_20260730
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision: str = "sub_instapay_20260802"
down_revision: str | Sequence[str] | None = "abandoned_fp_idx_20260730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription_payment_intents",
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
        sa.Column("plan_key", sa.String(20), nullable=False),
        sa.Column("billing_cycle", sa.String(10), nullable=False),
        sa.Column(
            "purpose",
            sa.String(20),
            nullable=False,
            server_default="new_subscription",
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="awaiting_proof"
        ),
        sa.Column("special_reference", sa.String(48), nullable=False),
        sa.Column("display_destination", sa.String(80), nullable=True),
        sa.Column("qr_payload", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
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
            "special_reference", name="uq_subscription_payment_intents_ref"
        ),
        schema="public",
    )
    op.create_index(
        "ix_subscription_payment_intents_status_expires",
        "subscription_payment_intents",
        ["status", "expires_at"],
        schema="public",
    )
    op.create_index(
        "ix_subscription_payment_intents_tenant_created",
        "subscription_payment_intents",
        ["tenant_id", "created_at"],
        schema="public",
    )

    op.create_table(
        "subscription_payment_proofs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.subscription_payment_intents.id", ondelete="CASCADE"),
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
            name="uq_subscription_proofs_tenant_image_hash",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "transaction_ref",
            name="uq_subscription_proofs_tenant_tx_ref",
        ),
        schema="public",
    )
    op.create_index(
        "ix_subscription_payment_proofs_intent",
        "subscription_payment_proofs",
        ["intent_id"],
        schema="public",
    )
    op.create_index(
        "ix_subscription_payment_proofs_status",
        "subscription_payment_proofs",
        ["status"],
        schema="public",
    )
    op.create_index(
        "ix_subscription_payment_proofs_tenant_phash",
        "subscription_payment_proofs",
        ["tenant_id", "perceptual_hash"],
        schema="public",
    )

    op.add_column(
        "billing_invoices",
        sa.Column(
            "subscription_payment_intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "public.subscription_payment_intents.id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column(
        "billing_invoices", "subscription_payment_intent_id", schema="public"
    )
    op.drop_index(
        "ix_subscription_payment_proofs_tenant_phash",
        table_name="subscription_payment_proofs",
        schema="public",
    )
    op.drop_index(
        "ix_subscription_payment_proofs_status",
        table_name="subscription_payment_proofs",
        schema="public",
    )
    op.drop_index(
        "ix_subscription_payment_proofs_intent",
        table_name="subscription_payment_proofs",
        schema="public",
    )
    op.drop_table("subscription_payment_proofs", schema="public")
    op.drop_index(
        "ix_subscription_payment_intents_tenant_created",
        table_name="subscription_payment_intents",
        schema="public",
    )
    op.drop_index(
        "ix_subscription_payment_intents_status_expires",
        table_name="subscription_payment_intents",
        schema="public",
    )
    op.drop_table("subscription_payment_intents", schema="public")

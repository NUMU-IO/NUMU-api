"""Merchant wallet — prepaid balance for the pay-as-you-go commission tier.

Platform-level (public schema, explicit tenant FK — billing.py pattern, NOT
TenantMixin): the wallet is money between NUMU and the tenant owner, shared
across all the tenant's stores.

``wallet_transactions`` is an append-only ledger and the source of truth;
``merchant_wallets.balance_cents`` is a denormalized snapshot maintained
under row lock by WalletService.apply_entry — never write it directly.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class MerchantWalletModel(Base, UUIDMixin, TimestampMixin):
    """One prepaid wallet per tenant. Created lazily on first use."""

    __tablename__ = "merchant_wallets"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Denormalized; ledger is authoritative. May be negative down to the
    # allowance (checkout gate) and further when the gate fails open.
    balance_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Provisional credits shown to the merchant while a manual top-up
    # receipt is under verification ("on hold"). NOT spendable — the gate
    # and commissions use balance_cents only — and never a ledger row:
    # verification releases the hold into a real topup ledger entry.
    pending_balance_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    # NULL -> settings.wallet_negative_allowance_cents
    negative_allowance_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL -> plan's commission_bps; set for negotiated per-tenant rates.
    commission_bps_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # active, suspended, exempt (WalletStatus)
    # Highest warning level already notified (0-3); dedupes the ladder.
    last_warning_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WalletTransactionModel(Base, UUIDMixin, TimestampMixin):
    """Append-only wallet ledger. Rows are never updated or deleted.

    Idempotency backbone: the partial unique indexes on (order_id, kind)
    make duplicate commission/reversal application a constraint violation
    (caught and treated as a no-op), and ``idempotency_key`` does the same
    for top-up credits and reconciliation charges.
    """

    __tablename__ = "wallet_transactions"
    __table_args__ = (
        Index("ix_wallet_transactions_wallet_created", "wallet_id", "created_at"),
        Index(
            "uq_wallet_tx_commission_order",
            "order_id",
            unique=True,
            postgresql_where=text("kind = 'commission'"),
        ),
        Index(
            "uq_wallet_tx_reversal_order",
            "order_id",
            unique=True,
            postgresql_where=text("kind = 'commission_reversal'"),
        ),
        UniqueConstraint("idempotency_key", name="uq_wallet_tx_idempotency_key"),
        {"schema": "public"},
    )

    wallet_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.merchant_wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # topup, commission, commission_reversal, adjustment
    # Signed: topup/reversal +, commission -, adjustment either.
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    # Plain column, no FK — orders can be purged, ledger must survive.
    order_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    topup_intent_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.wallet_topup_intents.id", ondelete="SET NULL"),
        nullable=True,
    )
    # paymob:{transaction_id} | proof:{proof_id} | recon:{order_id} | None
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Set for kind=adjustment: which admin did it. The ledger row IS the audit.
    actor_user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Original order currency/amount when converted, bps used, etc.
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class WalletTopupIntentModel(Base, UUIDMixin, TimestampMixin):
    """A merchant's declared intention to top up, one row per attempt."""

    __tablename__ = "wallet_topup_intents"
    __table_args__ = (
        Index("ix_wallet_topup_intents_status_expires", "status", "expires_at"),
        Index("ix_wallet_topup_intents_tenant_created", "tenant_id", "created_at"),
        UniqueConstraint("special_reference", name="uq_wallet_topup_intents_reference"),
        UniqueConstraint(
            "gateway_transaction_id", name="uq_wallet_topup_intents_gateway_tx"
        ),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    method: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # card, vodafone_cash, instapay (TopupMethod)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # TopupIntentStatus
    # Gateway merchant reference ("WTOP-{uuid}") or manual reference
    # ("WT-XXXXXX" InstaPay / "VC-XXXXXX" Vodafone Cash).
    special_reference: Mapped[str] = mapped_column(String(48), nullable=False)
    # Gateway-agnostic session/intent handles (Kashier session today).
    gateway_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gateway_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    gateway_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    # Manual-method payload: IPA (InstaPay) or wallet number (Vodafone Cash).
    display_destination: Mapped[str | None] = mapped_column(String(80), nullable=True)
    qr_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    credited_transaction_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.wallet_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class WalletTopupProofModel(Base, UUIDMixin, TimestampMixin):
    """Merchant-uploaded manual top-up receipt (InstaPay / Vodafone Cash).

    Deliberately a parallel table to ``payment_proofs`` (which hard-requires
    order_id/store_id and store-scoped dedup): the reusable parts of that
    pipeline are functions (sanitization, hashing, OCR, the pure rules
    engine), not the row shape. Dedup here is tenant-scoped.
    """

    __tablename__ = "wallet_topup_proofs"
    __table_args__ = (
        Index("ix_wallet_topup_proofs_intent", "topup_intent_id"),
        Index("ix_wallet_topup_proofs_status", "status"),
        Index("ix_wallet_topup_proofs_tenant_phash", "tenant_id", "perceptual_hash"),
        UniqueConstraint(
            "tenant_id",
            "proof_image_hash",
            name="uq_wallet_topup_proofs_tenant_image_hash",
        ),
        UniqueConstraint(
            "tenant_id",
            "transaction_ref",
            name="uq_wallet_topup_proofs_tenant_transaction_ref",
        ),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topup_intent_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.wallet_topup_intents.id", ondelete="CASCADE"),
        nullable=False,
    )
    proof_image_key: Mapped[str] = mapped_column(Text, nullable=False)
    proof_image_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    perceptual_hash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transaction_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="awaiting_review"
    )  # awaiting_review, approved, rejected (PaymentProofStatus values)
    review_decision_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_decision_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OCR enrichment (same columns/semantics as payment_proofs Phase C)
    ocr_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_extracted_amount_cents: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    ocr_extracted_ipa: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ocr_extracted_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_extracted_transaction_ref: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    ocr_extracted_recipient_name: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    ocr_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ocr_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auto_approval_block_reasons: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )

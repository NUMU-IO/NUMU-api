"""Subscription payments via InstaPay — intent + receipt proof.

Platform-level (public schema, explicit tenant FK — billing.py pattern,
NOT TenantMixin): a plan payment is money between NUMU and the tenant
owner, exactly like the merchant wallet.

Deliberately a parallel pair to ``wallet_topup_intents`` /
``wallet_topup_proofs`` (which are themselves parallel to the
order-scoped ``payment_proofs``): the reusable parts of the pipeline —
image sanitization, SHA-256/pHash, OCR, the pure ``auto_approval``
rules engine — are FUNCTIONS, not row shapes. The wallet pair credits a
balance; this pair activates/renews a subscription. Dedup is
tenant-scoped and additionally cross-checked against the wallet proofs
at submit time so one receipt can't fund both a top-up and a plan.
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
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class SubscriptionPaymentIntentModel(Base, UUIDMixin, TimestampMixin):
    """One row per InstaPay plan-payment attempt.

    ``amount_cents`` is a server-side snapshot of the plan price at
    creation time (piasters, from ``get_plan_features``) — the merchant
    never chooses it, which is what makes the OCR amount-match rule a
    hard guarantee rather than a heuristic.
    """

    __tablename__ = "subscription_payment_intents"
    __table_args__ = (
        Index("ix_subscription_payment_intents_status_expires", "status", "expires_at"),
        Index(
            "ix_subscription_payment_intents_tenant_created",
            "tenant_id",
            "created_at",
        ),
        UniqueConstraint(
            "special_reference", name="uq_subscription_payment_intents_ref"
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
    plan_key: Mapped[str] = mapped_column(String(20), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(10), nullable=False)
    # new_subscription | renewal (SubscriptionPaymentPurpose) — classified
    # server-side at creation from the tenant's current state.
    purpose: Mapped[str] = mapped_column(
        String(20), nullable=False, default="new_subscription"
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="awaiting_proof"
    )  # SubscriptionPaymentIntentStatus
    # Manual reference the merchant puts in the transfer note ("SUB-XXXXXX").
    special_reference: Mapped[str] = mapped_column(String(48), nullable=False)
    # NUMU's InstaPay IPA the merchant transfers to.
    display_destination: Mapped[str | None] = mapped_column(String(80), nullable=True)
    qr_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Stamped when the payment activated/renewed the subscription.
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SubscriptionPaymentProofModel(Base, UUIDMixin, TimestampMixin):
    """Merchant-uploaded InstaPay receipt for a subscription payment.

    Same OCR-enrichment block and tenant-scoped dedup uniques as
    ``wallet_topup_proofs`` so the shared vision/auto-approval functions
    and the admin review UI can treat both shapes identically.
    """

    __tablename__ = "subscription_payment_proofs"
    __table_args__ = (
        Index("ix_subscription_payment_proofs_intent", "intent_id"),
        Index("ix_subscription_payment_proofs_status", "status"),
        Index(
            "ix_subscription_payment_proofs_tenant_phash",
            "tenant_id",
            "perceptual_hash",
        ),
        UniqueConstraint(
            "tenant_id",
            "proof_image_hash",
            name="uq_subscription_proofs_tenant_image_hash",
        ),
        UniqueConstraint(
            "tenant_id",
            "transaction_ref",
            name="uq_subscription_proofs_tenant_tx_ref",
        ),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    intent_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.subscription_payment_intents.id", ondelete="CASCADE"),
        nullable=False,
    )
    proof_image_key: Mapped[str] = mapped_column(Text, nullable=False)
    proof_image_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    perceptual_hash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transaction_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="awaiting_review"
    )  # awaiting_review, auto_approved, approved, rejected
    review_decision_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_decision_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OCR enrichment (same columns/semantics as wallet_topup_proofs)
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

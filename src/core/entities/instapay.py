"""Domain entities for the InstaPay manual-verification flow.

InstaPay (Egypt's instant-payment network) has no merchant-facing API today,
so orders sit in PENDING while the customer pushes funds to the merchant's
IPA out-of-band and then uploads a screenshot + transaction reference. Two
objects back that workflow:

  * ``InstapayIntent`` — one per order. Stores the ref code, the snapshot
    of the merchant IPA, expiry deadline, and the pre-rendered QR payload.
  * ``PaymentProof`` — one-or-many per order (re-upload allowed after
    reject). Stores the uploaded screenshot key, its SHA-256 for dedup,
    the customer-supplied transaction reference, and the review decision.

These are framework-agnostic dataclasses; persistence lives in
``infrastructure/database/models/tenant/{instapay_intent,payment_proof}.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class InstapayIntentStatus(StrEnum):
    """Lifecycle of a single-order InstaPay intent."""

    AWAITING_PAYMENT = "awaiting_payment"
    PROOF_RECEIVED = "proof_received"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PaymentProofStatus(StrEnum):
    """Lifecycle of a customer-submitted payment proof."""

    AWAITING_REVIEW = "awaiting_review"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class InstapayIntent:
    """Per-order payload displayed to the customer at checkout."""

    id: UUID
    tenant_id: UUID
    store_id: UUID
    order_id: UUID
    reference_code: str
    display_ipa: str
    amount_cents: int
    expires_at: datetime
    qr_payload: str
    status: InstapayIntentStatus = InstapayIntentStatus.AWAITING_PAYMENT
    display_phone: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def new(
        cls,
        *,
        tenant_id: UUID,
        store_id: UUID,
        order_id: UUID,
        reference_code: str,
        display_ipa: str,
        amount_cents: int,
        expires_at: datetime,
        qr_payload: str,
        display_phone: str | None = None,
    ) -> InstapayIntent:
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            order_id=order_id,
            reference_code=reference_code,
            display_ipa=display_ipa,
            amount_cents=amount_cents,
            expires_at=expires_at,
            qr_payload=qr_payload,
            display_phone=display_phone,
        )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    def mark_proof_received(self) -> None:
        self.status = InstapayIntentStatus.PROOF_RECEIVED

    def mark_paid(self) -> None:
        self.status = InstapayIntentStatus.PAID

    def mark_expired(self) -> None:
        self.status = InstapayIntentStatus.EXPIRED

    def mark_cancelled(self) -> None:
        self.status = InstapayIntentStatus.CANCELLED


@dataclass
class PaymentProof:
    """Customer-submitted evidence of an out-of-band payment."""

    id: UUID
    tenant_id: UUID
    store_id: UUID
    order_id: UUID
    proof_image_key: str
    proof_image_hash: bytes
    transaction_ref: str
    status: PaymentProofStatus = PaymentProofStatus.AWAITING_REVIEW
    declared_amount_cents: int | None = None
    review_decision_by: UUID | None = None
    review_decision_at: datetime | None = None
    rejection_reason: str | None = None
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def new(
        cls,
        *,
        tenant_id: UUID,
        store_id: UUID,
        order_id: UUID,
        proof_image_key: str,
        proof_image_hash: bytes,
        transaction_ref: str,
        declared_amount_cents: int | None = None,
        idempotency_key: str | None = None,
    ) -> PaymentProof:
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            order_id=order_id,
            proof_image_key=proof_image_key,
            proof_image_hash=proof_image_hash,
            transaction_ref=transaction_ref,
            declared_amount_cents=declared_amount_cents,
            idempotency_key=idempotency_key,
        )

    def mark_auto_approved(self) -> None:
        self.status = PaymentProofStatus.AUTO_APPROVED
        self.review_decision_at = datetime.now(UTC)
        self.updated_at = self.review_decision_at

    def mark_approved(self, reviewer_id: UUID) -> None:
        self.status = PaymentProofStatus.APPROVED
        self.review_decision_by = reviewer_id
        self.review_decision_at = datetime.now(UTC)
        self.updated_at = self.review_decision_at

    def mark_rejected(self, reviewer_id: UUID | None, reason: str) -> None:
        self.status = PaymentProofStatus.REJECTED
        self.review_decision_by = reviewer_id
        self.review_decision_at = datetime.now(UTC)
        self.rejection_reason = reason
        self.updated_at = self.review_decision_at

    @property
    def can_retry(self) -> bool:
        return self.status == PaymentProofStatus.REJECTED

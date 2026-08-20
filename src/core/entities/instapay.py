"""Domain entities for the manual-verification ("push payment") flow.

Some Egyptian rails have no merchant-facing API, so orders sit in PENDING
while the customer pushes funds to the merchant out-of-band and then
uploads a screenshot + transaction reference. Two rails use this today
(see :class:`ManualPaymentMethod`):

  * **InstaPay** — funds land on the merchant's IPA (``merchant@cib``).
  * **Vodafone Cash** — funds land on the merchant's wallet number
    (``010…``). Same shape, different destination string, no QR (the
    customer dials ``*9#`` or uses the Ana Vodafone app).

Two objects back the workflow:

  * ``ManualPaymentIntent`` — one per order. Stores the ref code, the
    snapshot of the merchant's destination, expiry deadline, and — for
    InstaPay only — the pre-rendered QR payload.
  * ``PaymentProof`` — one-or-many per order (re-upload allowed after
    reject). Stores the uploaded screenshot key, its SHA-256 for dedup,
    the customer-supplied transaction reference, and the review decision.

``InstapayIntent`` / ``InstapayIntentStatus`` remain as aliases at the
bottom of this module: the entity predates the second rail, and keeping
the old names bound to the same objects means there is exactly ONE code
path rather than a fork per method.

These are framework-agnostic dataclasses; persistence lives in
``infrastructure/database/models/tenant/{instapay_intent,payment_proof}.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class ManualPaymentMethod(StrEnum):
    """Out-of-band rails NUMU notarizes rather than integrates.

    Stored as a plain ``varchar`` on ``instapay_intents.method`` (not a
    PG enum) so adding the third rail — bank transfer is the obvious
    next one — is an app-level change, not an ``ALTER TYPE`` that has
    to be coordinated with a deploy.
    """

    INSTAPAY = "instapay"
    VODAFONE_CASH = "vodafone_cash"

    @property
    def reference_prefix(self) -> str:
        """Short prefix for this rail's per-order reference code.

        Distinct per method so a merchant reading a transfer note can
        tell at a glance which rail it belongs to.
        """
        return _REFERENCE_PREFIXES[self]


_REFERENCE_PREFIXES: dict[ManualPaymentMethod, str] = {
    # "NU" predates the second rail and is baked into live reference
    # codes + merchant muscle memory — do not repurpose it.
    ManualPaymentMethod.INSTAPAY: "NU",
    ManualPaymentMethod.VODAFONE_CASH: "VF",
}


class ManualPaymentIntentStatus(StrEnum):
    """Lifecycle of a single-order manual-payment intent."""

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
class ManualPaymentIntent:
    """Per-order payload displayed to the customer at checkout.

    ``display_destination`` is the rail-specific string the customer
    sends money to — an IPA for InstaPay, a wallet number for Vodafone
    Cash. It is a *snapshot*: if the merchant later edits their wallet
    number, in-flight intents keep pointing at the destination the
    customer was actually shown.
    """

    id: UUID
    tenant_id: UUID
    store_id: UUID
    order_id: UUID
    reference_code: str
    display_destination: str
    amount_cents: int
    expires_at: datetime
    # Empty string for rails with no scannable payload (Vodafone Cash).
    qr_payload: str
    status: ManualPaymentIntentStatus = ManualPaymentIntentStatus.AWAITING_PAYMENT
    method: ManualPaymentMethod = ManualPaymentMethod.INSTAPAY
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
        display_destination: str,
        amount_cents: int,
        expires_at: datetime,
        qr_payload: str,
        method: ManualPaymentMethod = ManualPaymentMethod.INSTAPAY,
        display_phone: str | None = None,
    ) -> ManualPaymentIntent:
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            order_id=order_id,
            reference_code=reference_code,
            display_destination=display_destination,
            amount_cents=amount_cents,
            expires_at=expires_at,
            qr_payload=qr_payload,
            method=method,
            display_phone=display_phone,
        )

    @property
    def display_ipa(self) -> str:
        """Back-compat read alias for :attr:`display_destination`."""
        return self.display_destination

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    def mark_proof_received(self) -> None:
        self.status = ManualPaymentIntentStatus.PROOF_RECEIVED

    def mark_paid(self) -> None:
        self.status = ManualPaymentIntentStatus.PAID

    def mark_expired(self) -> None:
        self.status = ManualPaymentIntentStatus.EXPIRED

    def mark_cancelled(self) -> None:
        self.status = ManualPaymentIntentStatus.CANCELLED


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
    # 64-bit pHash of the sanitized image. ``None`` for rows
    # predating the Phase-A migration; the dedup layer simply skips
    # them when scanning for near-duplicates.
    perceptual_hash: int | None = None
    # Phase C OCR enrichment. ``ocr_status`` is one of
    # ``ok | skipped | failed`` when populated; the auto-approval
    # rules only act on ``ok`` rows so transient provider failures
    # never escalate into customer-visible behaviour.
    ocr_status: str | None = None
    ocr_extracted_amount_cents: int | None = None
    ocr_extracted_ipa: str | None = None
    ocr_raw_text: str | None = None
    ocr_provider: str | None = None
    ocr_processed_at: datetime | None = None
    # Phase C extras — the bank-app's note / transaction-ref / recipient
    # name as OCR'd. Each is paired with an opt-in merchant rule that
    # cross-checks against expected values (intent reference code,
    # submitted transaction_ref, registered name token). Nullable
    # everywhere — pre-extension rows simply have no signal.
    ocr_extracted_note: str | None = None
    ocr_extracted_transaction_ref: str | None = None
    ocr_extracted_recipient_name: str | None = None
    # Phase D — auto-approval rule reasons captured at submission time
    # (e.g. ``["ocr_amount_mismatch"]``). Empty / None for approved
    # proofs; the merchant review pane renders friendly copy per tag.
    auto_approval_block_reasons: list[str] | None = None
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
        perceptual_hash: int | None = None,
        ocr_status: str | None = None,
        ocr_extracted_amount_cents: int | None = None,
        ocr_extracted_ipa: str | None = None,
        ocr_raw_text: str | None = None,
        ocr_provider: str | None = None,
        ocr_processed_at: datetime | None = None,
        ocr_extracted_note: str | None = None,
        ocr_extracted_transaction_ref: str | None = None,
        ocr_extracted_recipient_name: str | None = None,
        auto_approval_block_reasons: list[str] | None = None,
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
            perceptual_hash=perceptual_hash,
            ocr_status=ocr_status,
            ocr_extracted_amount_cents=ocr_extracted_amount_cents,
            ocr_extracted_ipa=ocr_extracted_ipa,
            ocr_raw_text=ocr_raw_text,
            ocr_provider=ocr_provider,
            ocr_processed_at=ocr_processed_at,
            ocr_extracted_note=ocr_extracted_note,
            ocr_extracted_transaction_ref=ocr_extracted_transaction_ref,
            ocr_extracted_recipient_name=ocr_extracted_recipient_name,
            auto_approval_block_reasons=auto_approval_block_reasons,
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


# ── Back-compat aliases ──────────────────────────────────────────────
#
# The entity was born InstaPay-only. These names are bound to the very
# same objects (not subclasses, not copies), so old imports keep working
# and there is still exactly one implementation to maintain.
InstapayIntent = ManualPaymentIntent
InstapayIntentStatus = ManualPaymentIntentStatus

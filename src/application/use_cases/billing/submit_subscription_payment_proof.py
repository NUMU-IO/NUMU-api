"""Use case: merchant uploads an InstaPay receipt for a subscription payment.

Near-verbatim mirror of
:mod:`src.application.use_cases.wallet.submit_topup_proof` — same
sanitize → dedup → upload → OCR → rules → persist pipeline, different
row shape and a different success action (activate/renew the
subscription instead of crediting a wallet).

Two deliberate deviations from the wallet flow:

* **Cross-pipeline dedup** — the pre-check also scans
  ``wallet_topup_proofs``: one receipt must never fund both a top-up
  and a plan payment.
* **Subscription-sized auto-approval config** — the wallet thresholds
  (500 EGP / 5,000 EGP/day) would block every annual plan payment by
  amount alone. The amount here is server-fixed, so the threshold is
  raised to cover Pro annual; the three OCR match rules and the hard
  "no OCR amount → human review" downgrade still stand.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.billing.activate_instapay_subscription import (
    ActivationResult,
    activate_verified_subscription_payment,
)
from src.application.use_cases.wallet.submit_topup_proof import (
    _IntentShim,
    _ProofShim,
    platform_vision_service,
)
from src.core.entities.subscription_payment import (
    SubscriptionPaymentIntentStatus,
)
from src.core.interfaces.services.storage_service import (
    IStorageService,
    StorageBucket,
)
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
    SubscriptionPaymentProofModel,
)
from src.infrastructure.database.models.public.wallet import WalletTopupProofModel
from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
    AutoApprovalDecision,
    AutoApprovalFacts,
)
from src.infrastructure.external_services.instapay.auto_approval import (
    evaluate as evaluate_auto_approval,
)
from src.infrastructure.external_services.vision import (
    IProofVisionService,
    ProofVisionResult,
)
from src.infrastructure.repositories.payment_proof_repository import phash_to_db

logger = logging.getLogger(__name__)

# Pro annual is 499,000 piasters — the cap that matters. One order of
# magnitude above the wallet defaults because the amount is a server
# snapshot the merchant cannot inflate; the exposure is a forged
# receipt, and that is exactly what the OCR match rules gate on.
SUBSCRIPTION_AUTO_APPROVE_THRESHOLD_CENTS = 500_000  # 5,000 EGP
SUBSCRIPTION_AUTO_APPROVE_DAILY_CAP_CENTS = 2_500_000  # 25,000 EGP/day
SUBSCRIPTION_AUTO_APPROVE_DAILY_COUNT = 20


def subscription_auto_approval_config() -> AutoApprovalConfig:
    """Platform thresholds for subscription receipts (see module docstring)."""
    return AutoApprovalConfig(
        threshold_cents=SUBSCRIPTION_AUTO_APPROVE_THRESHOLD_CENTS,
        daily_cap_cents=SUBSCRIPTION_AUTO_APPROVE_DAILY_CAP_CENTS,
        daily_count_cap=SUBSCRIPTION_AUTO_APPROVE_DAILY_COUNT,
        amount_mismatch_tolerance_bps=100,
        require_ocr_amount_match=True,
        require_ocr_ipa_match=True,
        require_note_contains_reference=True,
    )


@dataclass
class SubmitSubscriptionProofResult:
    proof: SubscriptionPaymentProofModel
    intent: SubscriptionPaymentIntentModel
    decision: AutoApprovalDecision
    activation: ActivationResult | None  # set when auto-approved


class SubmitSubscriptionPaymentProofUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        storage_service: IStorageService,
        vision_service: IProofVisionService | None = None,
    ) -> None:
        self.session = session
        self.storage = storage_service
        self.vision = vision_service or platform_vision_service()

    async def execute(
        self,
        *,
        tenant_id: UUID,
        intent_id: UUID,
        image_bytes: bytes,
        image_content_type: str,
        transaction_ref: str,
        declared_amount_cents: int | None = None,
        image_perceptual_hash: int | None = None,
    ) -> SubmitSubscriptionProofResult:
        transaction_ref = transaction_ref.strip()
        if not transaction_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Transaction reference is required.",
            )

        intent = (
            await self.session.execute(
                select(SubscriptionPaymentIntentModel).where(
                    SubscriptionPaymentIntentModel.id == intent_id,
                    SubscriptionPaymentIntentModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if intent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription payment not found.",
            )
        if intent.status == SubscriptionPaymentIntentStatus.SUCCEEDED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This payment is already confirmed.",
            )
        if intent.status not in (
            SubscriptionPaymentIntentStatus.AWAITING_PROOF.value,
            SubscriptionPaymentIntentStatus.UNDER_REVIEW.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This payment can no longer accept a receipt.",
            )
        if intent.expires_at and datetime.now(UTC) >= intent.expires_at:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="The payment window for this subscription payment has expired.",
            )

        # ── Dedup pre-checks (tenant-scoped, BOTH proof pipelines) ──
        image_hash = hashlib.sha256(image_bytes).digest()
        dup = (
            await self.session.execute(
                select(SubscriptionPaymentProofModel.id).where(
                    SubscriptionPaymentProofModel.tenant_id == tenant_id,
                    (SubscriptionPaymentProofModel.proof_image_hash == image_hash)
                    | (
                        SubscriptionPaymentProofModel.transaction_ref == transaction_ref
                    ),
                )
            )
        ).first()
        if dup is None:
            # One receipt can't fund both a wallet top-up and a plan.
            dup = (
                await self.session.execute(
                    select(WalletTopupProofModel.id).where(
                        WalletTopupProofModel.tenant_id == tenant_id,
                        (WalletTopupProofModel.proof_image_hash == image_hash)
                        | (WalletTopupProofModel.transaction_ref == transaction_ref),
                    )
                )
            ).first()
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This screenshot or transaction reference was already submitted."
                ),
            )

        # ── Upload to R2 ───────────────────────────────────────────
        filename = (
            f"subscription-payments/{tenant_id}/{intent.id}/"
            f"{intent.special_reference}.bin"
        )
        uploaded = await self.storage.upload_file(
            file_content=image_bytes,
            filename=filename,
            content_type=image_content_type or "application/octet-stream",
            bucket=StorageBucket.PAYMENT_PROOFS,
        )

        async def _cleanup_uploaded() -> None:
            try:
                await self.storage.delete_file(uploaded.key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "subscription_proof_r2_cleanup_failed",
                    extra={"key": uploaded.key},
                )

        # ── OCR (soft-fail by contract) ────────────────────────────
        ocr: ProofVisionResult = await self.vision.extract(image_bytes)
        ocr_ok = ocr.status == "ok"

        # ── Auto-approval rules (subscription config + facts) ──────
        day_start = datetime.now(UTC) - timedelta(hours=24)
        daily_count, daily_cents = await self._daily_auto_approved(day_start)
        facts = AutoApprovalFacts(
            order_total_cents=intent.amount_cents,
            daily_auto_approved_count=daily_count,
            daily_auto_approved_cents=daily_cents,
            merchant_ipa=intent.display_destination,
            intent_reference_code=intent.special_reference,
            submitted_transaction_ref=transaction_ref,
        )
        proof_shim = _ProofShim(
            declared_amount_cents=declared_amount_cents,
            ocr_status=ocr.status if ocr.status != "skipped" else None,
            ocr_extracted_amount_cents=ocr.extracted_amount_cents,
            ocr_extracted_ipa=ocr.extracted_ipa,
            ocr_extracted_note=ocr.extracted_note,
            ocr_extracted_transaction_ref=ocr.extracted_transaction_ref,
            ocr_extracted_recipient_name=ocr.extracted_recipient_name,
        )
        decision = evaluate_auto_approval(
            intent=_IntentShim(expires_at=intent.expires_at),
            proof=proof_shim,
            config=subscription_auto_approval_config(),
            facts=facts,
        )
        # Same hard safety net as the wallet path (live incident
        # 2026-07-19): an unverified receipt must never auto-activate a
        # subscription. No OCR amount → a human decides.
        if decision.approved and not (
            ocr_ok and ocr.extracted_amount_cents is not None
        ):
            reason = "ocr_no_amount_found" if ocr_ok else "ocr_verification_unavailable"
            decision = AutoApprovalDecision(
                approved=False,
                reasons=[*decision.reasons, reason],
            )

        # ── Persist proof ──────────────────────────────────────────
        proof = SubscriptionPaymentProofModel(
            tenant_id=tenant_id,
            intent_id=intent.id,
            proof_image_key=uploaded.key,
            proof_image_hash=image_hash,
            perceptual_hash=phash_to_db(image_perceptual_hash),
            transaction_ref=transaction_ref,
            declared_amount_cents=declared_amount_cents,
            status="auto_approved" if decision.approved else "awaiting_review",
            ocr_status=proof_shim.ocr_status,
            ocr_extracted_amount_cents=ocr.extracted_amount_cents,
            ocr_extracted_ipa=ocr.extracted_ipa,
            ocr_extracted_note=ocr.extracted_note,
            ocr_extracted_transaction_ref=ocr.extracted_transaction_ref,
            ocr_extracted_recipient_name=ocr.extracted_recipient_name,
            ocr_raw_text=ocr.raw_text or None,
            ocr_provider=ocr.provider if ocr_ok else None,
            ocr_processed_at=ocr.processed_at if ocr_ok else None,
            auto_approval_block_reasons=(
                list(decision.reasons) if not decision.approved else None
            ),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(proof)
                await self.session.flush()
        except IntegrityError as exc:
            await _cleanup_uploaded()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This screenshot or transaction reference was already submitted."
                ),
            ) from exc

        activation: ActivationResult | None = None
        if decision.approved:
            activation = await activate_verified_subscription_payment(
                self.session, intent=intent
            )
            logger.info(
                "subscription_proof_auto_approved",
                extra={
                    "tenant_id": str(tenant_id),
                    "intent_id": str(intent.id),
                    "proof_id": str(proof.id),
                },
            )
        else:
            # No pending-hold equivalent here: there is no balance to
            # show — the Billing page renders the under_review state.
            intent.status = SubscriptionPaymentIntentStatus.UNDER_REVIEW.value
            logger.info(
                "subscription_proof_queued_for_review",
                extra={
                    "tenant_id": str(tenant_id),
                    "intent_id": str(intent.id),
                    "proof_id": str(proof.id),
                    "reasons": decision.reasons,
                },
            )

        await self.session.flush()
        return SubmitSubscriptionProofResult(
            proof=proof,
            intent=intent,
            decision=decision,
            activation=activation,
        )

    async def _daily_auto_approved(self, since: datetime) -> tuple[int, int]:
        """(count, cents) auto-approved subscription proofs in the window —
        platform-wide (the payee is NUMU), separate pool from wallet top-ups."""
        row = (
            await self.session.execute(
                select(
                    func.count(SubscriptionPaymentProofModel.id),
                    func.coalesce(
                        func.sum(SubscriptionPaymentIntentModel.amount_cents), 0
                    ),
                )
                .select_from(SubscriptionPaymentProofModel)
                .join(
                    SubscriptionPaymentIntentModel,
                    SubscriptionPaymentIntentModel.id
                    == SubscriptionPaymentProofModel.intent_id,
                )
                .where(
                    SubscriptionPaymentProofModel.status == "auto_approved",
                    SubscriptionPaymentProofModel.created_at >= since,
                )
            )
        ).one()
        return int(row[0]), int(row[1])

"""Use case: merchant uploads a manual top-up receipt (InstaPay / Vodafone Cash).

Mirrors :mod:`src.application.use_cases.payments.submit_payment_proof`
(the order-scoped original) but against NUMU's platform IPA and the
tenant-scoped ``wallet_topup_proofs`` table. The reusable pieces of that
pipeline — image sanitization (done by the route), SHA-256/pHash, OCR,
and the pure ``auto_approval.evaluate`` rules engine — are shared as
functions; only the row shape and the credit target differ.

Auto-approval facts here are platform-wide (settings), not per-store:
the payee is NUMU, so the thresholds protect NUMU.
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

from src.application.use_cases.wallet.credit_wallet import credit_topup_intent
from src.config.settings import get_settings
from src.core.entities.wallet import (
    MANUAL_TOPUP_METHODS,
    TopupIntentStatus,
)
from src.core.interfaces.services.storage_service import (
    IStorageService,
    StorageBucket,
)
from src.infrastructure.database.models.public.wallet import (
    WalletTopupIntentModel,
    WalletTopupProofModel,
    WalletTransactionModel,
)
from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
    AutoApprovalDecision,
    AutoApprovalFacts,
)
from src.infrastructure.external_services.instapay.auto_approval import (
    evaluate as evaluate_auto_approval,
)
from src.infrastructure.external_services.instapay.payment_service import (
    DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
    DEFAULT_AUTO_APPROVE_DAILY_COUNT,
    DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
)
from src.infrastructure.external_services.vision import (
    IProofVisionService,
    NoopProofVisionService,
    ProofVisionResult,
)
from src.infrastructure.repositories.payment_proof_repository import (
    phash_to_db,
)

logger = logging.getLogger(__name__)


@dataclass
class _IntentShim:
    """Duck-typed stand-in for the rules engine's ``intent`` parameter.

    ``evaluate()`` only calls ``intent.is_expired(now=...)``; giving it the
    top-up intent's expiry avoids force-fitting the order-scoped
    ``InstapayIntent`` entity (which requires order/store ids).
    """

    expires_at: datetime | None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at


@dataclass
class _ProofShim:
    """Duck-typed stand-in for the rules engine's ``proof`` parameter."""

    declared_amount_cents: int | None
    ocr_status: str | None
    ocr_extracted_amount_cents: int | None
    ocr_extracted_ipa: str | None
    ocr_extracted_note: str | None
    ocr_extracted_transaction_ref: str | None
    ocr_extracted_recipient_name: str | None


@dataclass
class SubmitTopupProofResult:
    proof: WalletTopupProofModel
    intent: WalletTopupIntentModel
    decision: AutoApprovalDecision
    credited_balance_cents: int | None  # set when auto-approved
    on_hold: bool = False  # amount added to pending_balance_cents


def platform_auto_approval_config() -> AutoApprovalConfig:
    """Platform-wide thresholds for top-up receipts.

    Reuses the InstaPay module defaults; the OCR cross-checks that make
    sense platform-side (amount, IPA, note-contains-reference) are ON —
    they no-op safely when the OCR provider is Noop.
    """
    return AutoApprovalConfig(
        threshold_cents=DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
        daily_cap_cents=DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
        daily_count_cap=DEFAULT_AUTO_APPROVE_DAILY_COUNT,
        amount_mismatch_tolerance_bps=100,
        require_ocr_amount_match=True,
        require_ocr_ipa_match=True,
        require_note_contains_reference=True,
    )


def platform_vision_service() -> IProofVisionService:
    """OCR provider for top-up receipts, from platform settings."""
    from src.api.dependencies.services import get_proof_vision_service_for_store

    provider = get_settings().platform_instapay_ocr_provider
    if not provider:
        return NoopProofVisionService()
    # Reuse the store-shaped factory with a synthetic settings dict so
    # provider selection / key checks stay in one place.
    return get_proof_vision_service_for_store({
        "payment": {"instapay": {"ocr_provider": provider}}
    })


class SubmitTopupProofUseCase:
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
        topup_intent_id: UUID,
        image_bytes: bytes,
        image_content_type: str,
        transaction_ref: str,
        declared_amount_cents: int | None = None,
        image_perceptual_hash: int | None = None,
    ) -> SubmitTopupProofResult:
        transaction_ref = transaction_ref.strip()
        if not transaction_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Transaction reference is required.",
            )

        intent = (
            await self.session.execute(
                select(WalletTopupIntentModel).where(
                    WalletTopupIntentModel.id == topup_intent_id,
                    WalletTopupIntentModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if intent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Top-up not found.",
            )
        if intent.method not in MANUAL_TOPUP_METHODS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This top-up method does not use receipt upload.",
            )
        if intent.status == TopupIntentStatus.SUCCEEDED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This top-up is already credited.",
            )
        if intent.status not in (
            TopupIntentStatus.AWAITING_PROOF.value,
            TopupIntentStatus.UNDER_REVIEW.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This top-up can no longer accept a receipt.",
            )
        if intent.expires_at and datetime.now(UTC) >= intent.expires_at:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="The payment window for this top-up has expired.",
            )

        # ── Dedup pre-checks (tenant-scoped) ───────────────────────
        image_hash = hashlib.sha256(image_bytes).digest()
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
            f"wallet-topups/{tenant_id}/{intent.id}/{intent.special_reference}.bin"
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
                    "wallet_proof_r2_cleanup_failed",
                    extra={"key": uploaded.key},
                )

        # ── OCR (soft-fail by contract) ────────────────────────────
        ocr: ProofVisionResult = await self.vision.extract(image_bytes)
        ocr_ok = ocr.status == "ok"

        # ── Auto-approval rules (platform config + facts) ──────────
        # The "IPA" fact is the intent's destination (IPA for InstaPay,
        # Vodafone Cash number for VC) — the OCR match rule no-ops when
        # the receipt doesn't expose a comparable value.
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
            config=platform_auto_approval_config(),
            facts=facts,
        )

        # ── Persist proof ──────────────────────────────────────────
        proof = WalletTopupProofModel(
            tenant_id=tenant_id,
            topup_intent_id=intent.id,
            proof_image_key=uploaded.key,
            proof_image_hash=image_hash,
            # Unsigned 64-bit hash → signed BIGINT range (else asyncpg
            # rejects any hash with the top bit set — ~half of real images).
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
            # add() inside the savepoint so a dedup-constraint rollback
            # expunges the pending row instead of poisoning the session.
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

        credited_balance: int | None = None
        on_hold = False
        if decision.approved:
            tx = await credit_topup_intent(
                self.session,
                intent=intent,
                idempotency_key=f"proof:{proof.id}",
                source=f"{intent.method}_auto_approval",
            )
            credited_balance = tx.balance_after_cents if tx else None
            logger.info(
                "wallet_topup_proof_auto_approved",
                extra={
                    "tenant_id": str(tenant_id),
                    "intent_id": str(intent.id),
                    "proof_id": str(proof.id),
                },
            )
        else:
            # Optimistic UX: the merchant sees the amount immediately as
            # ON HOLD while an admin verifies. Held only once per intent —
            # guarded by the awaiting_proof → under_review transition (a
            # rejected re-upload releases the hold first, see review use
            # case). Not spendable: gate/commissions read balance_cents.
            from src.application.services.wallet_service import WalletService

            if intent.status == TopupIntentStatus.AWAITING_PROOF.value:
                service = WalletService(self.session)
                wallet = await service.get_or_create_wallet(tenant_id, for_update=True)
                service.add_pending_hold(wallet, intent.amount_cents)
                on_hold = True
            intent.status = TopupIntentStatus.UNDER_REVIEW.value
            logger.info(
                "wallet_topup_proof_queued_for_review",
                extra={
                    "tenant_id": str(tenant_id),
                    "intent_id": str(intent.id),
                    "proof_id": str(proof.id),
                    "reasons": decision.reasons,
                },
            )

        await self.session.flush()
        return SubmitTopupProofResult(
            proof=proof,
            intent=intent,
            decision=decision,
            credited_balance_cents=credited_balance,
            on_hold=on_hold,
        )

    async def _daily_auto_approved(self, since: datetime) -> tuple[int, int]:
        """(count, cents) of auto-approved top-up proofs in the window —
        platform-wide, because the payee (and the fraud exposure) is NUMU."""
        row = (
            await self.session.execute(
                select(
                    func.count(WalletTopupProofModel.id),
                    func.coalesce(func.sum(WalletTransactionModel.amount_cents), 0),
                )
                .select_from(WalletTopupProofModel)
                .outerjoin(
                    WalletTransactionModel,
                    WalletTransactionModel.topup_intent_id
                    == WalletTopupProofModel.topup_intent_id,
                )
                .where(
                    WalletTopupProofModel.status == "auto_approved",
                    WalletTopupProofModel.created_at >= since,
                )
            )
        ).one()
        return int(row[0]), int(row[1])

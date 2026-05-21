"""Use case: customer uploads a payment proof for an InstaPay order.

Responsibilities (in order):

  1. Idempotency — a repeat request with the same ``idempotency_key``
     returns the first proof unchanged (network drops mid-upload
     mustn't create duplicate rows).
  2. Cross-tenant integrity — the order must belong to the customer,
     the store, and have an active InstapayIntent.
  3. Dedup — image SHA-256 and transaction reference must be unseen
     in this store (DB unique constraints are the last line of
     defence; we pre-check to return a clean 409).
  4. Storage — upload the screenshot to the PAYMENT_PROOFS bucket.
  5. Auto-approval — run the rules engine. On pass, flip the proof,
     intent, and order into the paid state and publish
     ``OrderPaidEvent``. On fail, leave the order PENDING so the
     merchant can review (or the customer can re-upload).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.core.entities.instapay import (
    InstapayIntent,
    PaymentProof,
    PaymentProofStatus,
)
from src.core.entities.order import Order, OrderStatus, PaymentStatus
from src.core.events.order_events import OrderPaidEvent
from src.core.events.payment_events import PaymentProofApprovedEvent
from src.core.interfaces.services.storage_service import (
    IStorageService,
    StorageBucket,
)
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)
from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
    AutoApprovalDecision,
    AutoApprovalFacts,
)
from src.infrastructure.external_services.instapay.auto_approval import (
    evaluate as evaluate_auto_approval,
)
from src.infrastructure.external_services.instapay.metrics import (
    proof_autoapprove_blocks_total,
    proof_review_latency_seconds,
    proof_submissions_total,
)
from src.infrastructure.external_services.vision import (
    IProofVisionService,
    NoopProofVisionService,
    ProofVisionResult,
)
from src.infrastructure.repositories.instapay_intent_repository import (
    InstapayIntentRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.payment_proof_repository import (
    PaymentProofRepository,
)

logger = get_logger(__name__)


def _extract_constraint_name(exc: IntegrityError) -> str | None:
    """Pull the violated constraint name out of an asyncpg IntegrityError.

    asyncpg exposes the underlying ``UniqueViolationError`` as
    ``exc.orig`` with a ``constraint_name`` attribute. Fall back to a
    string scan so a different driver (or a future wrapper) still
    returns *something* usable.
    """
    orig = getattr(exc, "orig", None)
    name = getattr(orig, "constraint_name", None)
    if name:
        return str(name)
    msg = str(orig or exc)
    for known in (
        "uq_payment_proofs_store_image_hash",
        "uq_payment_proofs_store_transaction_ref",
        "uq_payment_proofs_store_idempotency_key",
    ):
        if known in msg:
            return known
    return None


@dataclass
class SubmitPaymentProofResult:
    """What the route handler needs in order to shape the HTTP response."""

    proof: PaymentProof
    order: Order
    intent: InstapayIntent
    decision: AutoApprovalDecision
    signed_image_url: str
    created: bool  # False when served from idempotency


class SubmitPaymentProofUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        order_repo: OrderRepository,
        intent_repo: InstapayIntentRepository,
        proof_repo: PaymentProofRepository,
        storage_service: IStorageService,
    ) -> None:
        self.session = session
        self.order_repo = order_repo
        self.intent_repo = intent_repo
        self.proof_repo = proof_repo
        self.storage_service = storage_service

    async def execute(
        self,
        *,
        store_id: UUID,
        order_id: UUID,
        customer_id: UUID,
        image_bytes: bytes,
        image_content_type: str,
        transaction_ref: str,
        auto_approval_config: AutoApprovalConfig,
        declared_amount_cents: int | None = None,
        idempotency_key: str | None = None,
        # Phase A — pHash of the sanitized image, computed by the
        # route handler before this method runs. ``None`` means the
        # caller skipped sanitization (test paths only); the dedup
        # layer is then a no-op for this call.
        image_perceptual_hash: int | None = None,
        # Per-store Hamming-distance cutoff for the pHash dedup gate.
        # 5 of 64 bits ≈ 92% similarity — empirically rejects re-saves
        # and small crops, accepts genuine retries from a different
        # angle/screenshot session.
        perceptual_dedup_max_distance: int = 5,
        # Phase C — vision OCR provider picked by the store's admin
        # config, or a NoopProofVisionService when none is assigned.
        # Always non-None; ``None`` here would mean a caller didn't
        # construct one, which is a programming error.
        vision_service: IProofVisionService | None = None,
        # Merchant's stored InstaPay address — passed into the rules
        # engine so the OCR-IPA-match check can compare against it.
        # ``None`` means the rule no-ops, which is fine because the
        # rule is also gated on the merchant's opt-in flag.
        merchant_ipa: str | None = None,
        # Phase C extras — facts the new rules need. All optional;
        # missing values cause the matching rule to silently no-op.
        merchant_recipient_name_token: str | None = None,
    ) -> SubmitPaymentProofResult:
        log = logger.bind(
            store_id=str(store_id),
            order_id=str(order_id),
            customer_id=str(customer_id),
            idempotency_key=idempotency_key,
        )

        transaction_ref = transaction_ref.strip()
        if not transaction_ref:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Transaction reference is required.",
            )

        # ── Idempotency ────────────────────────────────────────────
        if idempotency_key:
            existing = await self.proof_repo.get_by_idempotency_key(
                store_id, idempotency_key
            )
            if existing is not None:
                log.info("proof_idempotent_replay")
                order = await self.order_repo.get_by_id(existing.order_id)
                intent = await self.intent_repo.get_by_order_id(existing.order_id)
                if order is None or intent is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotent proof references missing order/intent.",
                    )
                signed_url = await self.storage_service.get_signed_url(
                    existing.proof_image_key, expires_in=3600
                )
                decision = AutoApprovalDecision(
                    approved=existing.status
                    in (
                        PaymentProofStatus.AUTO_APPROVED,
                        PaymentProofStatus.APPROVED,
                    ),
                    reasons=[existing.rejection_reason]
                    if existing.rejection_reason
                    else [],
                )
                return SubmitPaymentProofResult(
                    proof=existing,
                    order=order,
                    intent=intent,
                    decision=decision,
                    signed_image_url=signed_url,
                    created=False,
                )

        # ── Load order + intent (fail fast before touching R2) ─────
        order = await self.order_repo.get_by_id(order_id)
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found.",
            )
        if order.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Order does not belong to this customer.",
            )
        if order.payment_status == PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Order is already paid.",
            )
        # (#7) Cancelled orders are terminal — accepting a proof here
        # would let a customer push funds to a closed order and then
        # expect fulfilment; force them to contact the merchant instead.
        if order.status == OrderStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Order is cancelled. Please contact the merchant.",
            )
        # (#8) Defensive: refuse a proof for an order that wasn't placed
        # as InstaPay. Two valid paths reach this code with a real proof:
        #   1. Full InstaPay checkout — ``payment_method == "instapay"``.
        #   2. COD-with-deposit where the customer chose InstaPay as the
        #      deposit gateway — ``payment_method == "cod"`` AND
        #      ``deposit_gateway == "instapay"``. The order is COD; the
        #      deposit (a precursor) is what the proof attests to.
        # Any other combination is genuinely off-flow and we reject. The
        # matching ``InstapayIntent`` check below is the actual safety
        # net; this method-level check just yields a clearer error.
        is_full_instapay = order.payment_method == "instapay"
        is_instapay_deposit = (
            order.payment_method == "cod" and order.deposit_gateway == "instapay"
        )
        if order.payment_method and not (is_full_instapay or is_instapay_deposit):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This order was not placed with InstaPay.",
            )

        intent = await self.intent_repo.get_by_order_id(order_id)
        if intent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No InstaPay intent exists for this order.",
            )
        # (#4) Reject expired intents *before* we spend R2 bandwidth
        # on the upload. The Celery sweeper will transition the intent
        # and cancel the order; we just stop the customer short here.
        if intent.is_expired():
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="The payment window for this order has expired.",
            )

        # ── Dedup pre-checks (cheap — fail fast before R2 upload) ──
        image_hash = hashlib.sha256(image_bytes).digest()
        if await self.proof_repo.image_hash_exists(order.store_id, image_hash):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This screenshot has already been submitted for this store.",
            )
        if await self.proof_repo.transaction_ref_exists(
            order.store_id, transaction_ref
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This transaction reference has already been used.",
            )

        # NOTE: We deliberately don't gate submissions on perceptual-hash
        # neighbours. InstaPay's success-screen UI is a fixed template
        # — same merchant + same amount + same recipient produces
        # near-identical pHashes for two genuinely different
        # transactions, so any pHash gate (hard 409 or soft block)
        # is ~100% false-positive in this domain. The actual replay
        # surface is covered by the SHA-256 + transaction_ref unique
        # constraints above (exact-byte replay and ref reuse) plus
        # the OCR transaction-ref match rule (catches image reuse with
        # a freshly-typed ref — OCR'd ref will differ from typed). The
        # pHash column is still computed and stored for forensics and
        # the Phase B "possibly related submissions" merchant-review
        # hint, just not acted on at submission time.

        # ── Upload to R2 ───────────────────────────────────────────
        filename = f"{order.store_id}/{order_id}/{intent.reference_code}.bin"
        uploaded = await self.storage_service.upload_file(
            file_content=image_bytes,
            filename=filename,
            content_type=image_content_type or "application/octet-stream",
            bucket=StorageBucket.PAYMENT_PROOFS,
        )

        # From here on, any raise/early-return must clean up the R2
        # object we just wrote — otherwise we leak on the race paths.
        async def _cleanup_uploaded() -> None:
            try:
                await self.storage_service.delete_file(uploaded.key)
            except Exception:
                log.warning("payment_proof_r2_cleanup_failed", key=uploaded.key)

        # ── OCR (Phase C) — runs once on the sanitized bytes ───────
        # Soft-fail by contract: any provider error returns a
        # ``ProofVisionResult.failed`` and the auto-approval rules
        # silently no-op for non-OK statuses. We still persist the
        # status + provider so observability can split provider
        # health from "feature off". When no service was injected
        # (test paths) we use a Noop directly so the rest of the
        # method stays uniform.
        vision: IProofVisionService = vision_service or NoopProofVisionService()
        ocr_result: ProofVisionResult = await vision.extract(image_bytes)

        # (#1) Serialize the cap evaluation + proof write per store so
        # two concurrent uploads can't both see "count=9, cap=10" and
        # both auto-approve. pg_advisory_xact_lock is released on the
        # enclosing commit/rollback of this request — no cleanup code
        # needed, no new table, no schema change. Blocking is brief
        # because the critical section is just a few queries.
        store_lock_key = (
            int(
                hashlib.blake2b(
                    f"instapay_cap:{order.store_id}".encode(), digest_size=8
                ).hexdigest(),
                16,
            )
            & 0x7FFFFFFFFFFFFFFF
        )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": store_lock_key},
        )

        # Re-read stats inside the lock so a concurrent auto-approval
        # that just committed is visible. Depends on the session being
        # in READ COMMITTED isolation (PostgreSQL's default) — each
        # statement gets a fresh snapshot, so the read after the lock
        # acquire sees any rows committed by waiters who just released.
        day_start = datetime.now(UTC) - timedelta(hours=24)
        daily_count, daily_cents = await self.proof_repo.daily_auto_approve_stats(
            order.store_id, since=day_start
        )
        facts = AutoApprovalFacts(
            order_total_cents=order.total,
            daily_auto_approved_count=daily_count,
            daily_auto_approved_cents=daily_cents,
            merchant_ipa=merchant_ipa,
            # Phase C extras — the new rules need these facts to
            # compare against ``proof.ocr_extracted_*`` fields.
            intent_reference_code=intent.reference_code,
            submitted_transaction_ref=transaction_ref,
            merchant_recipient_name_token=merchant_recipient_name_token,
        )
        # OCR fields land on both the speculative proof (so the
        # rules engine sees them) and the persisted row below. We
        # store ``ocr_status`` only when it's meaningful (skipped
        # rows persist as NULL — they're indistinguishable from
        # pre-Phase-C rows for query purposes).
        ocr_persist_status = (
            ocr_result.status if ocr_result.status != "skipped" else None
        )
        ocr_persist_provider = (
            ocr_result.provider if ocr_result.status != "skipped" else None
        )
        ocr_persist_processed_at = (
            ocr_result.processed_at if ocr_result.status != "skipped" else None
        )
        decision = evaluate_auto_approval(
            intent=intent,
            proof=PaymentProof.new(
                tenant_id=order.tenant_id,
                store_id=order.store_id,
                order_id=order.id,
                proof_image_key=uploaded.key,
                proof_image_hash=image_hash,
                transaction_ref=transaction_ref,
                declared_amount_cents=declared_amount_cents,
                perceptual_hash=image_perceptual_hash,
                ocr_status=ocr_persist_status,
                ocr_extracted_amount_cents=ocr_result.extracted_amount_cents,
                ocr_extracted_ipa=ocr_result.extracted_ipa,
                ocr_raw_text=ocr_result.raw_text or None,
                ocr_provider=ocr_persist_provider,
                ocr_processed_at=ocr_persist_processed_at,
                ocr_extracted_note=ocr_result.extracted_note,
                ocr_extracted_transaction_ref=ocr_result.extracted_transaction_ref,
                ocr_extracted_recipient_name=ocr_result.extracted_recipient_name,
            ),
            config=auto_approval_config,
            facts=facts,
        )

        # ── Persist proof ──────────────────────────────────────────
        proof = PaymentProof.new(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            proof_image_key=uploaded.key,
            proof_image_hash=image_hash,
            transaction_ref=transaction_ref,
            declared_amount_cents=declared_amount_cents,
            idempotency_key=idempotency_key,
            perceptual_hash=image_perceptual_hash,
            ocr_status=ocr_persist_status,
            ocr_extracted_amount_cents=ocr_result.extracted_amount_cents,
            ocr_extracted_ipa=ocr_result.extracted_ipa,
            ocr_raw_text=ocr_result.raw_text or None,
            ocr_provider=ocr_persist_provider,
            ocr_processed_at=ocr_persist_processed_at,
            ocr_extracted_note=ocr_result.extracted_note,
            ocr_extracted_transaction_ref=ocr_result.extracted_transaction_ref,
            ocr_extracted_recipient_name=ocr_result.extracted_recipient_name,
            # Persist the rule-engine reasons so the merchant review
            # pane can render them. Approved proofs leave NULL — the UI
            # only shows the panel when there's something to explain.
            auto_approval_block_reasons=(
                list(decision.reasons) if not decision.approved else None
            ),
        )
        # Metric: rule breakdown on soft blocks. Fires once per
        # reason so dashboards can pivot on which rule trips most —
        # a daily-cap dominant reason is the signal to raise the cap.
        if not decision.approved:
            for reason in decision.reasons:
                proof_autoapprove_blocks_total.inc(
                    reason=reason, store_id=str(order.store_id)
                )

        if decision.approved:
            proof.mark_auto_approved()

        # (#2) The pre-check above closes the common case but two
        # concurrent uploads can still slip through. When the DB
        # unique constraint fires, we must also clean up the R2 object
        # we just wrote — otherwise the bucket grows unbounded.
        try:
            proof = await self.proof_repo.create(proof)
        except IntegrityError as exc:
            await _cleanup_uploaded()
            constraint = _extract_constraint_name(exc)
            if constraint == "uq_payment_proofs_store_image_hash":
                detail = "This screenshot has already been submitted for this store."
            elif constraint == "uq_payment_proofs_store_transaction_ref":
                detail = "This transaction reference has already been used."
            elif constraint == "uq_payment_proofs_store_idempotency_key":
                detail = (
                    "Duplicate request — please wait for the first upload to finish."
                )
            else:
                detail = "Could not save this proof. Please retry."
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=detail
            ) from exc

        # ── Transition intent + order if approved ──────────────────
        if decision.approved:
            intent.mark_paid()
            await self.intent_repo.update(intent)

            order.mark_as_paid(
                payment_id=intent.reference_code,
                payment_method="instapay",
            )
            # Keep all InstaPay-specific metadata under a single sub-dict
            # so future additions don't scatter keys across the Order
            # blob and so a later cleanup (or field removal) is a single
            # dict delete.
            instapay_meta = dict(order.metadata.get("instapay") or {})
            instapay_meta["reference_code"] = intent.reference_code
            instapay_meta["auto_approved"] = True
            instapay_meta["proof_id"] = str(proof.id)
            order.metadata["instapay"] = instapay_meta
            await self.order_repo.update(order)

            self.session.add(
                PaymentTransactionModel(
                    tenant_id=order.tenant_id,
                    store_id=order.store_id,
                    order_id=order.id,
                    channel="online",
                    gateway="instapay",
                    display_name=f"InstaPay {intent.display_ipa}",
                    amount_cents=order.total,
                    currency=order.currency,
                    status="success",
                    gateway_transaction_id=intent.reference_code,
                    processing_completed_at=datetime.now(UTC),
                )
            )
            await self.session.flush()

            try:
                from src.infrastructure.events.setup import get_event_bus

                bus = get_event_bus()
                bus.publish(
                    OrderPaidEvent(
                        order_id=order.id,
                        order_number=order.order_number,
                        store_id=order.store_id,
                        customer_id=order.customer_id,
                        payment_id=intent.reference_code,
                        payment_method="instapay",
                        total=float(order.total),
                    )
                )
                # Separate event drives the short "payment received"
                # email independently of invoice generation — see
                # handle_payment_proof_approved.
                bus.publish(
                    PaymentProofApprovedEvent(
                        proof_id=proof.id,
                        order_id=order.id,
                        order_number=order.order_number,
                        tenant_id=order.tenant_id,
                        store_id=order.store_id,
                        customer_id=order.customer_id,
                        reference_code=intent.reference_code,
                        amount_cents=order.total,
                        currency=order.currency,
                        auto_approved=True,
                    )
                )
            except Exception:
                log.exception("order_paid_event_publish_failed")

            proof_submissions_total.inc(
                status="auto_approved", store_id=str(order.store_id)
            )
            # Zero-latency observation — dashboards that bucket by
            # decision type still see the auto-approve volume.
            proof_review_latency_seconds.observe(
                0.0, decision="auto_approved", store_id=str(order.store_id)
            )
            log.info(
                "instapay_proof_auto_approved",
                reference_code=intent.reference_code,
                proof_id=str(proof.id),
            )
        else:
            intent.mark_proof_received()
            await self.intent_repo.update(intent)
            proof_submissions_total.inc(
                status="awaiting_review", store_id=str(order.store_id)
            )
            log.info(
                "instapay_proof_queued_for_review",
                reference_code=intent.reference_code,
                proof_id=str(proof.id),
                reasons=decision.reasons,
            )

        # No signed URL here — the storefront doesn't render the
        # uploaded image back and the merchant sees it via the
        # dedicated list-proofs endpoint which signs on demand.
        # Skipping saves one S3 API call on every successful submission.
        return SubmitPaymentProofResult(
            proof=proof,
            order=order,
            intent=intent,
            decision=decision,
            signed_image_url="",
            created=True,
        )

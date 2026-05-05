"""Use case: merchant approves/rejects a queued InstaPay proof.

Called from the merchant dashboard. Approval flips the order into PAID +
publishes ``OrderPaidEvent`` (same end-state as auto-approval, so
downstream handlers — invoice generation, email, shipment creation —
don't need to branch on review path). Rejection records the reason and
leaves the order in PENDING so the customer can re-upload within the
intent's expiry window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.core.entities.instapay import (
    InstapayIntent,
    PaymentProof,
    PaymentProofStatus,
)
from src.core.entities.order import Order, PaymentStatus
from src.core.events.order_events import OrderPaidEvent
from src.core.events.payment_events import (
    PaymentProofApprovedEvent,
    PaymentProofRejectedEvent,
)
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)
from src.infrastructure.external_services.instapay.metrics import (
    proof_review_latency_seconds,
    proof_submissions_total,
)
from src.infrastructure.repositories.instapay_intent_repository import (
    InstapayIntentRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.payment_proof_repository import (
    PaymentProofRepository,
)

logger = get_logger(__name__)


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass
class ReviewPaymentProofResult:
    proof: PaymentProof
    order: Order
    intent: InstapayIntent


class ReviewPaymentProofUseCase:
    def __init__(
        self,
        *,
        session: AsyncSession,
        order_repo: OrderRepository,
        intent_repo: InstapayIntentRepository,
        proof_repo: PaymentProofRepository,
    ) -> None:
        self.session = session
        self.order_repo = order_repo
        self.intent_repo = intent_repo
        self.proof_repo = proof_repo

    async def execute(
        self,
        *,
        proof_id: UUID,
        reviewer_user_id: UUID,
        decision: ReviewDecision,
        rejection_reason: str | None = None,
    ) -> ReviewPaymentProofResult:
        log = logger.bind(
            proof_id=str(proof_id),
            reviewer_id=str(reviewer_user_id),
            decision=decision.value,
        )

        proof = await self.proof_repo.get_by_id(proof_id)
        if proof is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payment proof not found.",
            )
        if proof.status != PaymentProofStatus.AWAITING_REVIEW:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Proof is already {proof.status.value}; cannot review again.",
            )

        order = await self.order_repo.get_by_id(proof.order_id)
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found for this proof.",
            )
        intent = await self.intent_repo.get_by_order_id(proof.order_id)
        if intent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="InstaPay intent not found for this proof.",
            )

        if decision == ReviewDecision.APPROVE:
            if order.payment_status == PaymentStatus.PAID:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Order is already paid.",
                )

            proof.mark_approved(reviewer_user_id)
            await self.proof_repo.update(proof)

            intent.mark_paid()
            await self.intent_repo.update(intent)

            order.mark_as_paid(
                payment_id=intent.reference_code,
                payment_method="instapay",
            )
            instapay_meta = dict(order.metadata.get("instapay") or {})
            instapay_meta["reference_code"] = intent.reference_code
            instapay_meta["reviewed_by"] = str(reviewer_user_id)
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
                        auto_approved=False,
                    )
                )
            except Exception:
                log.exception("order_paid_event_publish_failed")

            # Merchant decision latency + counter. created_at on the
            # proof is the upload timestamp; the delta is what SLA
            # alerting keys on.
            decision_seconds = max(
                0.0,
                (datetime.now(UTC) - proof.created_at).total_seconds(),
            )
            proof_review_latency_seconds.observe(
                decision_seconds,
                decision="approved",
                store_id=str(order.store_id),
            )
            proof_submissions_total.inc(status="approved", store_id=str(order.store_id))
            log.info("instapay_proof_approved")
        else:
            if not rejection_reason or not rejection_reason.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="A reason is required when rejecting a proof.",
                )
            proof.mark_rejected(reviewer_user_id, rejection_reason.strip())
            await self.proof_repo.update(proof)

            # Customer notification — rejection with reason + retry CTA.
            # can_retry is tied to the intent's remaining window: past
            # expiry the storefront won't accept a new proof anyway, so
            # the email shouldn't promise a retry that would 410.
            can_retry = not intent.is_expired()
            try:
                from src.infrastructure.events.setup import get_event_bus

                get_event_bus().publish(
                    PaymentProofRejectedEvent(
                        proof_id=proof.id,
                        order_id=order.id,
                        order_number=order.order_number,
                        tenant_id=order.tenant_id,
                        store_id=order.store_id,
                        customer_id=order.customer_id,
                        reference_code=intent.reference_code,
                        rejection_reason=rejection_reason.strip(),
                        can_retry=can_retry,
                    )
                )
            except Exception:
                log.exception("instapay_reject_event_publish_failed")

            decision_seconds = max(
                0.0,
                (datetime.now(UTC) - proof.created_at).total_seconds(),
            )
            proof_review_latency_seconds.observe(
                decision_seconds,
                decision="rejected",
                store_id=str(order.store_id),
            )
            proof_submissions_total.inc(status="rejected", store_id=str(order.store_id))
            log.info("instapay_proof_rejected", reason=rejection_reason)

        return ReviewPaymentProofResult(proof=proof, order=order, intent=intent)

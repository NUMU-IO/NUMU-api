"""Use case: admin approves / rejects an under-review subscription receipt.

Mirrors :mod:`src.application.use_cases.wallet.review_topup_proof`.
Approval activates through the SAME helper the auto-approval path uses;
the intent-status guard inside it makes a double-click or a race with a
parallel auto-approve a logged no-op, never a double activation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.billing.activate_instapay_subscription import (
    ActivationResult,
    activate_verified_subscription_payment,
)
from src.core.entities.subscription_payment import SubscriptionPaymentIntentStatus
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
    SubscriptionPaymentProofModel,
)

logger = logging.getLogger(__name__)


@dataclass
class ReviewSubscriptionProofResult:
    proof: SubscriptionPaymentProofModel
    intent: SubscriptionPaymentIntentModel
    activation: ActivationResult | None


class ReviewSubscriptionPaymentProofUseCase:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _load(
        self, proof_id: UUID
    ) -> tuple[SubscriptionPaymentProofModel, SubscriptionPaymentIntentModel]:
        proof = (
            await self.db.execute(
                select(SubscriptionPaymentProofModel).where(
                    SubscriptionPaymentProofModel.id == proof_id
                )
            )
        ).scalar_one_or_none()
        if proof is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Proof not found.",
            )
        if proof.status not in ("awaiting_review",):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Proof is already {proof.status}.",
            )
        intent = (
            await self.db.execute(
                select(SubscriptionPaymentIntentModel).where(
                    SubscriptionPaymentIntentModel.id == proof.intent_id
                )
            )
        ).scalar_one()
        return proof, intent

    async def approve(
        self, *, proof_id: UUID, admin_user_id: UUID
    ) -> ReviewSubscriptionProofResult:
        proof, intent = await self._load(proof_id)

        proof.status = "approved"
        proof.review_decision_by = admin_user_id
        proof.review_decision_at = datetime.now(UTC)

        activation = await activate_verified_subscription_payment(
            self.db, intent=intent
        )
        logger.info(
            "subscription_proof_approved",
            extra={
                "proof_id": str(proof.id),
                "intent_id": str(intent.id),
                "admin_user_id": str(admin_user_id),
                "activated": activation.activated,
            },
        )
        return ReviewSubscriptionProofResult(
            proof=proof, intent=intent, activation=activation
        )

    async def reject(
        self, *, proof_id: UUID, admin_user_id: UUID, reason: str
    ) -> ReviewSubscriptionProofResult:
        proof, intent = await self._load(proof_id)

        proof.status = "rejected"
        proof.review_decision_by = admin_user_id
        proof.review_decision_at = datetime.now(UTC)
        proof.rejection_reason = reason.strip() or "Rejected by admin"

        # Give the merchant another shot while the window is open;
        # otherwise the expiry sweep closes the intent.
        intent.status = SubscriptionPaymentIntentStatus.AWAITING_PROOF.value
        intent.failure_reason = proof.rejection_reason

        await self.db.flush()
        logger.info(
            "subscription_proof_rejected",
            extra={
                "proof_id": str(proof.id),
                "intent_id": str(intent.id),
                "admin_user_id": str(admin_user_id),
                "reason": proof.rejection_reason,
            },
        )
        return ReviewSubscriptionProofResult(
            proof=proof, intent=intent, activation=None
        )

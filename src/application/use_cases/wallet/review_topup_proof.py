"""Use case: admin approves / rejects an under-review wallet top-up proof.

Mirrors :mod:`src.application.use_cases.payments.review_payment_proof`
(order-scoped) for the platform top-up queue. Approval credits through
the same idempotency key the auto-approval path would have used
(``proof:{proof_id}``), so a webhook replay / double-click can never
double-credit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.wallet_service import WalletService
from src.application.use_cases.wallet.credit_wallet import credit_topup_intent
from src.core.entities.wallet import TopupIntentStatus
from src.infrastructure.database.models.public.wallet import (
    WalletTopupIntentModel,
    WalletTopupProofModel,
)

logger = logging.getLogger(__name__)


@dataclass
class ReviewTopupProofResult:
    proof: WalletTopupProofModel
    intent: WalletTopupIntentModel
    credited_balance_cents: int | None


class ReviewTopupProofUseCase:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _load(
        self, proof_id: UUID
    ) -> tuple[WalletTopupProofModel, WalletTopupIntentModel]:
        proof = (
            await self.db.execute(
                select(WalletTopupProofModel).where(
                    WalletTopupProofModel.id == proof_id
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
                select(WalletTopupIntentModel).where(
                    WalletTopupIntentModel.id == proof.topup_intent_id
                )
            )
        ).scalar_one()
        return proof, intent

    async def approve(
        self, *, proof_id: UUID, admin_user_id: UUID
    ) -> ReviewTopupProofResult:
        proof, intent = await self._load(proof_id)

        proof.status = "approved"
        proof.review_decision_by = admin_user_id
        proof.review_decision_at = datetime.now(UTC)

        # The optimistic hold becomes real money: release the pending
        # amount and write the settled ledger credit.
        service = WalletService(self.db)
        wallet = await service.get_or_create_wallet(intent.tenant_id, for_update=True)
        if intent.status == TopupIntentStatus.UNDER_REVIEW.value:
            service.release_pending_hold(wallet, intent.amount_cents)

        tx = await credit_topup_intent(
            self.db,
            intent=intent,
            idempotency_key=f"proof:{proof.id}",
            source="admin_review",
            actor_user_id=admin_user_id,
        )
        logger.info(
            "wallet_topup_proof_approved",
            extra={
                "proof_id": str(proof.id),
                "intent_id": str(intent.id),
                "admin_user_id": str(admin_user_id),
            },
        )
        return ReviewTopupProofResult(
            proof=proof,
            intent=intent,
            credited_balance_cents=tx.balance_after_cents if tx else None,
        )

    async def reject(
        self, *, proof_id: UUID, admin_user_id: UUID, reason: str
    ) -> ReviewTopupProofResult:
        proof, intent = await self._load(proof_id)

        proof.status = "rejected"
        proof.review_decision_by = admin_user_id
        proof.review_decision_at = datetime.now(UTC)
        proof.rejection_reason = reason.strip() or "Rejected by admin"

        # Drop the optimistic hold — the merchant's on-hold credit
        # disappears (they were never able to spend it).
        service = WalletService(self.db)
        wallet = await service.get_or_create_wallet(intent.tenant_id, for_update=True)
        if intent.status == TopupIntentStatus.UNDER_REVIEW.value:
            service.release_pending_hold(wallet, intent.amount_cents)

        # Give the merchant another shot while the window is open;
        # otherwise the expiry sweep will close the intent.
        intent.status = TopupIntentStatus.AWAITING_PROOF.value
        intent.failure_reason = proof.rejection_reason

        await self.db.flush()
        logger.info(
            "wallet_topup_proof_rejected",
            extra={
                "proof_id": str(proof.id),
                "intent_id": str(intent.id),
                "admin_user_id": str(admin_user_id),
                "reason": proof.rejection_reason,
            },
        )
        return ReviewTopupProofResult(
            proof=proof, intent=intent, credited_balance_cents=None
        )

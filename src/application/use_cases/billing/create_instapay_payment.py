"""Use case: merchant starts an InstaPay subscription payment.

Mirrors :meth:`CreateTopupUseCase._create_manual` (the wallet flow) but
for plan payments: the amount is a SERVER-SIDE snapshot of the plan
price — the merchant never types it, which is what lets the OCR
amount-match rule act as a hard verification instead of a heuristic.

Destination is NUMU's platform IPA from the admin wallet settings (one
receiving account for both top-ups and subscriptions — ops reconciles
by the reference prefix: WT- top-ups, SUB- subscriptions).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.plan import get_plan_features
from src.core.entities.subscription_payment import (
    BILLING_CYCLES,
    INSTAPAY_PAYABLE_PLANS,
    SUBSCRIPTION_PAYMENT_EXPIRY_MINUTES,
    SubscriptionPaymentIntentStatus,
    SubscriptionPaymentPurpose,
)
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
)
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)

logger = logging.getLogger(__name__)


@dataclass
class CreateSubscriptionPaymentResult:
    intent: SubscriptionPaymentIntentModel


def classify_purpose(tenant: TenantModel, plan: str, billing_cycle: str) -> str:
    """Renewal iff the tenant is already running EXACTLY this plan+cycle.

    Anything else — trial, read_only, a different plan, a cycle switch —
    is a (re/new) subscription and goes through ``SubscribeUseCase`` so
    the full activation block (lifecycle, expiry reset, trial conversion
    stamp) runs.
    """
    if (
        tenant.plan == plan
        and (tenant.billing_cycle or "monthly") == billing_cycle
        and tenant.lifecycle_state in (TenantLifecycleState.ACTIVE.value, "past_due")
        and tenant.next_renewal_at is not None
    ):
        return SubscriptionPaymentPurpose.RENEWAL.value
    return SubscriptionPaymentPurpose.NEW_SUBSCRIPTION.value


class CreateSubscriptionPaymentIntentUseCase:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute(
        self,
        *,
        tenant: TenantModel,
        user_id: UUID,
        plan: str,
        billing_cycle: str,
    ) -> CreateSubscriptionPaymentResult:
        from src.application.services.wallet_settings import get_wallet_settings
        from src.infrastructure.external_services.instapay.payment_service import (
            generate_reference_code,
        )
        from src.infrastructure.external_services.instapay.qr_generator import (
            build_qr_payload,
        )

        if plan not in INSTAPAY_PAYABLE_PLANS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This plan cannot be paid via InstaPay.",
            )
        if billing_cycle not in BILLING_CYCLES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="billing_cycle must be monthly or annual.",
            )

        features = get_plan_features(plan)
        amount_cents = (
            features.annual_price_piasters
            if billing_cycle == "annual"
            else features.monthly_price_piasters
        )
        if amount_cents <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This plan has no fixed price.",
            )

        admin = await get_wallet_settings(self.db)
        destination = admin.instapay_ipa
        if not destination:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="InstaPay payments are not configured.",
            )

        # One open payment at a time: resuming beats minting duplicates —
        # the OCR reference/amount checks are per-intent, and two live
        # references for one tenant just invites paying against the
        # stale one. 409 carries the open intent id so the hub resumes it.
        open_intent = (
            await self.db.execute(
                select(SubscriptionPaymentIntentModel).where(
                    SubscriptionPaymentIntentModel.tenant_id == tenant.id,
                    SubscriptionPaymentIntentModel.status.in_((
                        SubscriptionPaymentIntentStatus.AWAITING_PROOF.value,
                        SubscriptionPaymentIntentStatus.UNDER_REVIEW.value,
                    )),
                )
            )
        ).scalar_one_or_none()
        if open_intent is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "A subscription payment is already in progress.",
                    "open_intent_id": str(open_intent.id),
                    "open_intent_status": open_intent.status,
                },
            )

        # Retry the short code on the (rare) unique collision.
        reference = generate_reference_code(prefix="SUB")
        for _ in range(3):
            exists = (
                await self.db.execute(
                    select(SubscriptionPaymentIntentModel.id).where(
                        SubscriptionPaymentIntentModel.special_reference == reference
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                break
            reference = generate_reference_code(prefix="SUB")

        qr_payload = build_qr_payload(
            ipa=destination,
            amount_cents=amount_cents,
            reference_code=reference,
            note=f"NUMU {features.display_name} {reference}",
        )

        expires_at = datetime.now(UTC) + timedelta(
            minutes=SUBSCRIPTION_PAYMENT_EXPIRY_MINUTES
        )
        intent = SubscriptionPaymentIntentModel(
            tenant_id=tenant.id,
            created_by_user_id=user_id,
            plan_key=plan,
            billing_cycle=billing_cycle,
            purpose=classify_purpose(tenant, plan, billing_cycle),
            amount_cents=amount_cents,
            currency="EGP",
            status=SubscriptionPaymentIntentStatus.AWAITING_PROOF.value,
            special_reference=reference,
            display_destination=destination,
            qr_payload=qr_payload,
            expires_at=expires_at,
        )
        self.db.add(intent)
        await self.db.flush()

        logger.info(
            "subscription_payment_intent_created",
            extra={
                "tenant_id": str(tenant.id),
                "intent_id": str(intent.id),
                "plan": plan,
                "billing_cycle": billing_cycle,
                "purpose": intent.purpose,
                "amount_cents": amount_cents,
            },
        )
        return CreateSubscriptionPaymentResult(intent=intent)

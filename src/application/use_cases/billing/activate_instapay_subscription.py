"""Shared activation for a VERIFIED InstaPay subscription payment.

Called from exactly two places — the auto-approval path in
``submit_subscription_payment_proof`` and the admin approve path in
``review_subscription_payment_proof`` — so a webhookless double-fire
(admin double-click racing an auto-approve) can never activate twice:
the intent-status guard is the idempotency primitive.

Branching:

* ``new_subscription`` (and any intent whose tenant is NOT currently
  active) → ``SubscribeUseCase`` with ``payment_method="instapay_verified"``
  so the full activation block runs (lifecycle, expiry reset,
  trial_converted_at, invoice).
* ``renewal`` — or a "new" intent whose tenant became ACTIVE in the
  meantime (paid by card mid-flow, admin approved a parallel intent) —
  → extend ``next_renewal_at`` by one cycle from the CURRENT anchor and
  write the linked invoice directly. ``SubscribeUseCase`` would
  early-return for an ACTIVE tenant and swallow the payment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.subscription_payment import (
    SubscriptionPaymentIntentStatus,
    SubscriptionPaymentPurpose,
)
from src.infrastructure.database.models.public.billing import BillingInvoiceModel
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
)
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)

logger = logging.getLogger(__name__)


@dataclass
class ActivationResult:
    activated: bool  # False = replay no-op (intent already terminal)
    tenant: TenantModel | None
    next_renewal_at: datetime | None


def _period_delta(cycle: str) -> timedelta:
    return timedelta(days=365) if cycle == "annual" else timedelta(days=30)


async def activate_verified_subscription_payment(
    db: AsyncSession,
    *,
    intent: SubscriptionPaymentIntentModel,
) -> ActivationResult:
    """Apply a verified payment to the tenant. Caller owns the commit."""
    if intent.status not in (
        SubscriptionPaymentIntentStatus.AWAITING_PROOF.value,
        SubscriptionPaymentIntentStatus.UNDER_REVIEW.value,
    ):
        # Replay (double-approve, retried request): already handled.
        logger.info(
            "subscription_payment_activation_replay_noop",
            extra={"intent_id": str(intent.id), "status": intent.status},
        )
        return ActivationResult(activated=False, tenant=None, next_renewal_at=None)

    tenant = (
        await db.execute(
            select(TenantModel)
            .where(TenantModel.id == intent.tenant_id)
            .with_for_update()
        )
    ).scalar_one()

    now = datetime.now(UTC)
    is_renewal_shaped = (
        intent.purpose == SubscriptionPaymentPurpose.RENEWAL.value
        or tenant.lifecycle_state == TenantLifecycleState.ACTIVE.value
    )

    if is_renewal_shaped:
        # Anchor on the current renewal date so paying early never loses
        # days; a past_due anchor just means the new period starts where
        # the old one ended (mirrors the renewal task's period math).
        period_start = tenant.next_renewal_at or now
        period_end = period_start + _period_delta(intent.billing_cycle)
        db.add(
            BillingInvoiceModel(
                tenant_id=tenant.id,
                period_start=period_start,
                period_end=period_end,
                amount_cents=intent.amount_cents,
                currency=intent.currency,
                status="paid",
                subscription_payment_intent_id=intent.id,
                paid_at=now,
            )
        )
        tenant.plan = intent.plan_key
        tenant.billing_cycle = intent.billing_cycle
        tenant.next_renewal_at = period_end
        tenant.renewal_retry_count = 0
        tenant.lifecycle_state = TenantLifecycleState.ACTIVE.value
    else:
        from src.application.use_cases.billing.subscribe import SubscribeUseCase

        await SubscribeUseCase(db).execute(
            tenant_id=tenant.id,
            plan=intent.plan_key,
            billing_cycle=intent.billing_cycle,
            payment_method="instapay_verified",
            subscription_payment_intent_id=intent.id,
        )
        # Re-read the tenant fields SubscribeUseCase just set (same
        # session — this is the identity-mapped instance, already fresh).

    intent.status = SubscriptionPaymentIntentStatus.SUCCEEDED.value
    intent.activated_at = now
    await db.flush()

    logger.info(
        "subscription_payment_activated",
        extra={
            "intent_id": str(intent.id),
            "tenant_id": str(tenant.id),
            "plan": intent.plan_key,
            "billing_cycle": intent.billing_cycle,
            "purpose": intent.purpose,
            "renewal_shaped": is_renewal_shaped,
        },
    )
    return ActivationResult(
        activated=True, tenant=tenant, next_renewal_at=tenant.next_renewal_at
    )

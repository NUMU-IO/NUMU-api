"""Celery task — process due subscription renewals (backend-005).

Runs hourly. Walks ``public.tenants`` for rows where
``lifecycle_state ∈ {ACTIVE, PAST_DUE} AND next_renewal_at <= now()``
and re-charges the merchant's stored card token via
``PaymobRecurringBillingService``. Tenants without a stored token
(InstaPay-paid subscriptions) go straight to dunning: the nudge email
and the hub's "renewal due" banner point them at the manual InstaPay
re-pay flow on /billing (purpose=renewal extends the period on
verification). Merchant wallets are deliberately NOT touched here —
the wallet is the payg tier's funding instrument, not a subscription
payment method.

Outcomes per tenant:
  * Success: write paid invoice, advance ``next_renewal_at``,
    reset ``renewal_retry_count`` to 0, lifecycle back to ``ACTIVE``.
  * Failure (retry available): increment retry count, push
    ``next_renewal_at`` +24h, set lifecycle to ``PAST_DUE``, and enqueue
    a bilingual "renewal due — pay by card or InstaPay" email nudge.
  * Failure exhausted (>=3 retries AND ≥72h since
    ``subscription_started_at``): transition to ``READ_ONLY`` via
    the existing ``TenantService.transition_to_read_only`` path.

The dunning trio (retries / backoff / window) is admin-tunable via
``billing_lifecycle_settings``. Each tenant runs in its own try/except
so a single failure cannot abort the batch.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

# Operational tunables. The dunning trio (retries / backoff / window)
# are DEFAULTS only — the sweep reads the effective values from the
# admin-tunable ``billing_lifecycle_settings`` at the start of each run
# (see src/application/services/billing_settings.py).
MAX_BATCH_SIZE = 100
MAX_RETRIES_BEFORE_READ_ONLY = 3
DUNNING_WINDOW_HOURS = 72
RETRY_BACKOFF_HOURS = 24


def _run(batch_size: int) -> dict:
    return asyncio.run(_async_run(batch_size))


async def _async_run(batch_size: int) -> dict:  # noqa: PLR0915 - linear flow
    from sqlalchemy import select

    from src.application.services.paymob_recurring_billing_service import (
        PaymobRecurringBillingService,
        RecurringChargeFailure,
        RecurringChargeSuccess,
    )
    from src.config.settings import get_settings
    from src.core.entities.plan import get_plan_features
    from src.infrastructure.database.connection import (
        AsyncSessionLocal as async_session_factory,
    )
    from src.infrastructure.database.models.public.billing import (
        BillingInvoiceModel,
    )
    from src.infrastructure.database.models.public.tenant import (
        TenantLifecycleState,
        TenantModel,
    )
    from src.infrastructure.external_services.paymob.payment_service import (
        PaymobPaymentService,
    )
    from src.infrastructure.tenancy.service import TenantService

    settings = get_settings()
    paymob_service = PaymobPaymentService(
        secret_key=getattr(settings, "platform_paymob_secret_key", None),
        public_key=getattr(settings, "platform_paymob_public_key", None),
        hmac_secret=getattr(settings, "platform_paymob_hmac_secret", None),
        card_integration_id=getattr(
            settings, "platform_paymob_card_integration_id", None
        ),
    )
    recurring = PaymobRecurringBillingService(paymob_service=paymob_service)

    encryption_key_id = getattr(settings, "credential_encryption_key_id", "v1")

    succeeded = 0
    failed = 0
    moved_to_read_only = 0
    skipped = 0

    async with async_session_factory() as session:
        now = datetime.now(UTC)

        # Admin-tunable dunning ladder (falls back to the module defaults
        # when no override is stored).
        from src.application.services.billing_settings import get_billing_settings

        lifecycle_cfg = await get_billing_settings(session, use_cache=False)
        max_retries = lifecycle_cfg.dunning_max_retries
        retry_backoff_hours = lifecycle_cfg.dunning_retry_backoff_hours
        dunning_window_hours = lifecycle_cfg.dunning_window_hours

        q = (
            select(TenantModel)
            .where(
                TenantModel.lifecycle_state.in_([
                    TenantLifecycleState.ACTIVE.value,
                    "past_due",  # not yet in the enum; persisted as raw string
                ]),
                TenantModel.next_renewal_at.is_not(None),
                TenantModel.next_renewal_at <= now,
            )
            .limit(batch_size)
        )
        due = (await session.execute(q)).scalars().all()

        for tenant in due:
            try:
                features = get_plan_features(tenant.plan)
                cycle = tenant.billing_cycle or "monthly"
                amount = (
                    features.annual_price_piasters
                    if cycle == "annual"
                    else features.monthly_price_piasters
                )
                if amount <= 0:
                    # Plan is free / custom contract — nothing to charge.
                    tenant.next_renewal_at = now + _period_delta(cycle)
                    tenant.renewal_retry_count = 0
                    skipped += 1
                    continue

                period_start = tenant.next_renewal_at or now

                # GRANDFATHER GUARD — existing merchants must not be
                # affected by the InstaPay-subscriptions rollout. Before
                # it, tenants without a stored card token were SKIPPED
                # here (renewal_skipped_no_token), never dunned. Keep
                # exactly that for tenants who never went through the
                # InstaPay payment flow; only tenants who actually paid
                # via InstaPay (a succeeded intent exists) opt in to
                # dunning-by-InstaPay when their period lapses.
                if not tenant.paymob_card_token_encrypted and not (
                    await _has_succeeded_instapay_payment(session, tenant.id)
                ):
                    skipped += 1
                    logger.warning(
                        "renewal_skipped_no_token",
                        extra={"tenant_id": str(tenant.id)},
                    )
                    continue

                # Card charge — the ONLY automatic collection path.
                # No stored token (InstaPay-paid subscription) → dunning,
                # whose email/banner point at the manual InstaPay re-pay.
                paymob_tx_id: str | None = None
                charged = False
                failure_reason = "no_card_token"
                if tenant.paymob_card_token_encrypted:
                    idem_ref = f"renewal-{tenant.id}-{period_start.isoformat()}"
                    result = await recurring.charge_subscription(
                        tenant_id=tenant.id,
                        amount_cents=amount,
                        currency="EGP",
                        encrypted_card_token=tenant.paymob_card_token_encrypted,
                        key_id=encryption_key_id,
                        idempotency_ref=idem_ref,
                    )
                    if isinstance(result, RecurringChargeSuccess):
                        charged = True
                        paymob_tx_id = result.transaction_id
                    elif isinstance(result, RecurringChargeFailure):
                        failure_reason = result.reason

                if charged:
                    period_end = period_start + _period_delta(cycle)
                    invoice = BillingInvoiceModel(
                        tenant_id=tenant.id,
                        period_start=period_start,
                        period_end=period_end,
                        amount_cents=amount,
                        currency="EGP",
                        status="paid",
                        paymob_transaction_id=paymob_tx_id,
                        paid_at=now,
                    )
                    session.add(invoice)
                    tenant.next_renewal_at = period_end
                    tenant.renewal_retry_count = 0
                    tenant.lifecycle_state = TenantLifecycleState.ACTIVE.value
                    succeeded += 1
                    logger.info(
                        "renewal_succeeded",
                        extra={
                            "tenant_id": str(tenant.id),
                            "amount_cents": amount,
                            "transaction_id": paymob_tx_id,
                        },
                    )
                else:
                    tenant.renewal_retry_count = (tenant.renewal_retry_count or 0) + 1
                    tenant.lifecycle_state = "past_due"
                    tenant.next_renewal_at = now + timedelta(hours=retry_backoff_hours)

                    started = tenant.subscription_started_at or period_start
                    elapsed = now - started
                    exhausted = (
                        tenant.renewal_retry_count >= max_retries
                        and elapsed >= timedelta(hours=dunning_window_hours)
                    )
                    if exhausted:
                        tenant_service = TenantService(session)
                        await tenant_service.transition_to_read_only(
                            tenant, reason="payment_failed"
                        )
                        moved_to_read_only += 1
                        logger.warning(
                            "renewal_exhausted_moved_to_read_only",
                            extra={
                                "tenant_id": str(tenant.id),
                                "retries": tenant.renewal_retry_count,
                                "reason": failure_reason,
                            },
                        )
                    else:
                        failed += 1
                        logger.info(
                            "renewal_failed_will_retry",
                            extra={
                                "tenant_id": str(tenant.id),
                                "retries": tenant.renewal_retry_count,
                                "reason": failure_reason,
                            },
                        )
                        # Best-effort nudge: how to pay (card / wallet
                        # top-up / InstaPay on /billing). Broker failure
                        # must never fail the sweep.
                        try:
                            from src.infrastructure.messaging.tasks.subscription_payment_tasks import (  # noqa: E501
                                send_renewal_payment_due_task,
                            )

                            send_renewal_payment_due_task.delay(
                                tenant_id=str(tenant.id),
                                amount_cents=amount,
                                retry_count=tenant.renewal_retry_count,
                            )
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "renewal_nudge_enqueue_failed",
                                extra={"tenant_id": str(tenant.id)},
                            )
            except Exception:
                logger.exception(
                    "renewal_unhandled_error",
                    extra={"tenant_id": str(tenant.id)},
                )
                failed += 1

        await session.commit()

    return {
        "succeeded": succeeded,
        "failed": failed,
        "moved_to_read_only": moved_to_read_only,
        "skipped": skipped,
    }


def _period_delta(cycle: str) -> timedelta:
    return timedelta(days=365) if cycle == "annual" else timedelta(days=30)


async def _has_succeeded_instapay_payment(session, tenant_id) -> bool:  # noqa: ANN001
    """True if the tenant ever completed an InstaPay subscription payment.

    The opt-in signal for the new renewal lifecycle: legacy tenants
    (admin-activated, discount-code activations, pre-rollout states)
    have no such intent and keep the historical skip behavior.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models.public.subscription_payment import (
        SubscriptionPaymentIntentModel,
    )

    row = (
        await session.execute(
            select(SubscriptionPaymentIntentModel.id)
            .where(
                SubscriptionPaymentIntentModel.tenant_id == tenant_id,
                SubscriptionPaymentIntentModel.status == "succeeded",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


@celery_app.task(
    name="tasks.process_due_renewals",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def process_due_renewals(  # type: ignore[no-untyped-def]
    self, batch_size: int = MAX_BATCH_SIZE
) -> dict:
    """Charge stored card tokens for tenants whose period has elapsed."""
    try:
        logger.info("Starting subscription renewal sweep …")
        result = _run(batch_size)
        logger.info("Renewal sweep complete: %s", result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("Renewal sweep failed, retrying …")
        raise self.retry(exc=exc)

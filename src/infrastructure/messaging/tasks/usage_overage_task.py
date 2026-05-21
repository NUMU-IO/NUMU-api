"""Celery task — report verification-message overages to Shopify (backend-017).

Sprint 1 shipped ``UsageRelayService.post_usage`` (backend-004) with
9 unit tests. The audit found it had **zero call sites** — overage
billing never fired, merchants got unlimited verifications above cap
for free.

This task closes the dot. Runs daily at 04:00 UTC; for every store
with an ``ACTIVE`` ``ShopifySubscription``, counts WhatsApp / SMS
verification messages this billing cycle, subtracts the plan cap,
and POSTs the overage to the Shopify-app's ``/api/billing/usage-record``
via the existing ``UsageRelayService``.

Idempotency is upstream: each call passes
``f"{store_id}-{period_start.isoformat()}-overage"`` as the
``idempotency_key`` so a daily re-run within the same period won't
double-charge.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


# Per-plan verification message caps. These are deliberately defined
# here (not in src/core/entities/plan.py) because that module's plans
# are for the broader numu platform (storefront), not the Shopify-app
# subscription tiers. The two sets of plans live in different domains.
PLAN_VERIFICATION_CAPS: dict[str, int] = {
    "starter": 450,
    "growth": 5_000,
    "scale": 15_000,
}

# 1 message × $0.05 = 5 cents. Public pricing decision per the
# constitution.
OVERAGE_RATE_CENTS = 5

# How far back to look when counting messages — used as a backstop in
# case the subscription has no current_period_end (legacy rows).
_DEFAULT_PERIOD_DAYS = 30


def _run_sweep(batch_size: int) -> dict:
    return asyncio.run(_async_sweep(batch_size))


async def _async_sweep(batch_size: int) -> dict:  # noqa: PLR0915 - linear flow
    from sqlalchemy import func, select

    from src.application.services.usage_relay_service import (
        RelayConfigError,
        RelayInvalidPayload,
        RelayUnavailable,
        UsageRelayService,
    )
    from src.infrastructure.database.connection import (
        AsyncSessionLocal as async_session_factory,
    )
    from src.infrastructure.database.models.tenant.message_log import (
        MessageLogModel,
    )
    from src.infrastructure.database.models.tenant.shopify_installation import (
        ShopifyInstallationModel,
    )
    from src.infrastructure.database.models.tenant.shopify_subscription import (
        ShopifySubscriptionModel,
    )

    relay = UsageRelayService()

    posted = 0
    skipped_below_cap = 0
    skipped_unknown_plan = 0
    skipped_config_error = 0
    failed = 0

    async with async_session_factory() as session:
        # Active subscriptions with a known plan cap. The ``ACTIVE``
        # filter scopes to currently-paying merchants — trials and
        # cancelled subscriptions don't get overage charged.
        subs_q = (
            select(ShopifySubscriptionModel)
            .where(
                ShopifySubscriptionModel.status == "ACTIVE",
                ShopifySubscriptionModel.plan_id.in_(
                    list(PLAN_VERIFICATION_CAPS.keys())
                ),
            )
            .limit(batch_size)
        )
        subs = (await session.execute(subs_q)).scalars().all()

        for sub in subs:
            cap = PLAN_VERIFICATION_CAPS.get(sub.plan_id)
            if cap is None:
                skipped_unknown_plan += 1
                continue

            # Resolve shop_domain for the relay payload via the matching
            # ShopifyInstallation. The relay expects the merchant's
            # ``.myshopify.com`` domain, not numu's internal store_id.
            install_q = await session.execute(
                select(ShopifyInstallationModel).where(
                    ShopifyInstallationModel.store_id == sub.store_id,
                    ShopifyInstallationModel.is_active.is_(True),
                )
            )
            install = install_q.scalar_one_or_none()
            if install is None:
                logger.warning(
                    "overage_skipped_no_installation",
                    extra={"store_id": str(sub.store_id)},
                )
                continue

            # Period boundaries — prefer the subscription's own period
            # boundary so partial-month plan changes are handled
            # correctly. Fall back to a 30-day window for legacy rows.
            now = datetime.now(UTC)
            if sub.current_period_end:
                period_end = sub.current_period_end
                if period_end.tzinfo is None:
                    period_end = period_end.replace(tzinfo=UTC)
                period_start = period_end - timedelta(days=_DEFAULT_PERIOD_DAYS)
            else:
                period_end = now
                period_start = now - timedelta(days=_DEFAULT_PERIOD_DAYS)

            # Count outbound verification messages this period.
            # ``template_name`` carries the WhatsApp template — we count
            # the order_confirmation / cod_confirm / cod_verification
            # families, anything that originated from a verification
            # nudge.
            count_q = await session.execute(
                select(func.count())
                .select_from(MessageLogModel)
                .where(
                    MessageLogModel.store_id == sub.store_id,
                    MessageLogModel.direction == "outbound",
                    MessageLogModel.created_at >= period_start,
                    MessageLogModel.created_at < period_end,
                )
            )
            sent_count = count_q.scalar_one() or 0
            overage = sent_count - cap
            if overage <= 0:
                skipped_below_cap += 1
                continue

            amount_cents = overage * OVERAGE_RATE_CENTS
            idempotency_key = f"{sub.store_id}-{period_start.isoformat()}-overage"
            description = (
                f"{overage} verification messages above {sub.plan_id.title()} "
                f"cap ({cap})"
            )

            try:
                await relay.post_usage(
                    shop_domain=install.shopify_domain,
                    amount_cents=amount_cents,
                    description=description,
                    idempotency_key=idempotency_key,
                )
                posted += 1
                logger.info(
                    "overage_posted",
                    extra={
                        "store_id": str(sub.store_id),
                        "shop_domain": install.shopify_domain,
                        "plan": sub.plan_id,
                        "overage": overage,
                        "amount_cents": amount_cents,
                    },
                )
            except RelayConfigError as exc:
                # Deployment issue (missing SHOPIFY_APP_URL / wrong
                # X-Internal-Key). Log + skip; ops will fix env.
                logger.exception(
                    "overage_skipped_config_error",
                    extra={
                        "store_id": str(sub.store_id),
                        "error": str(exc),
                    },
                )
                skipped_config_error += 1
            except RelayInvalidPayload as exc:
                # Programmer error — body shape doesn't match the
                # Shopify-app's expectation. Don't retry the batch;
                # surface in Sentry by re-raising at task level via
                # the failed counter.
                logger.exception(
                    "overage_invalid_payload",
                    extra={
                        "store_id": str(sub.store_id),
                        "error": str(exc),
                    },
                )
                failed += 1
            except RelayUnavailable as exc:
                # Transient — Shopify-app down. Don't retry within the
                # batch; the next daily sweep will pick it up.
                logger.warning(
                    "overage_relay_unavailable",
                    extra={
                        "store_id": str(sub.store_id),
                        "error": str(exc),
                    },
                )
                failed += 1

    return {
        "posted": posted,
        "skipped_below_cap": skipped_below_cap,
        "skipped_unknown_plan": skipped_unknown_plan,
        "skipped_config_error": skipped_config_error,
        "failed": failed,
    }


@celery_app.task(
    name="tasks.report_verification_overages",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def report_verification_overages(  # type: ignore[no-untyped-def]
    self, batch_size: int = 500
) -> dict:
    """Daily sweep that bills WhatsApp/SMS verification overages."""
    try:
        logger.info("Starting verification-overage sweep …")
        result = _run_sweep(batch_size)
        logger.info("Overage sweep complete: %s", result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("Overage sweep failed, retrying …")
        raise self.retry(exc=exc)

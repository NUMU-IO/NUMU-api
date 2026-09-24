"""Renew paid-app subscriptions whose period has ended (apps plan, Phase 7).

Access already follows ``current_period_end`` (app_billing.is_entitled), so a
store stops being served the moment its period ends whether or not this has
run. This charges the next period from the wallet, credits the partner's 80%,
and marks what can't renew (cancelled, uninstalled, wallet short) so the hub
shows the truth.

Hourly, like the WhatsApp access expiry: a renewal that lands hours late reads
as "the app broke" to a merchant.
"""

import asyncio

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


async def _renew() -> dict:
    from src.application.services.app_billing import WalletChargeSource, renew_due
    from src.application.services.notification_feed import (
        emit_notification_standalone,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    notices: list[dict] = []
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        source = WalletChargeSource(session)
        stats = await renew_due(session, source=source, notices=notices)
        await session.commit()
        await source.invalidate()
    for n in notices:
        await emit_notification_standalone(**n)
    return stats


async def _warn_trials() -> int:
    from src.application.services.app_billing import trial_ending_notices
    from src.application.services.notification_feed import (
        emit_notification_standalone,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        notices = await trial_ending_notices(session)
    sent = 0
    for n in notices:
        sent += bool(await emit_notification_standalone(**n))
    return sent


@celery_app.task(
    name="tasks.renew_app_subscriptions",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def renew_app_subscriptions_task(self):
    """Charge the next period of every paid-app subscription that ended."""
    try:
        stats = _run_async(_renew())
        if any(stats.values()):
            logger.info("app_subscription_renewal_sweep", **stats)
        return stats
    except Exception as exc:
        logger.exception("app_subscription_renewal_failed")
        raise self.retry(exc=exc)


@celery_app.task(name="tasks.warn_app_trials_ending", bind=True, max_retries=2)
def warn_app_trials_ending_task(self):
    """Tell merchants a paid app's free trial ends within 3 days (once each)."""
    try:
        return {"sent": _run_async(_warn_trials())}
    except Exception as exc:
        logger.exception("app_trial_warning_failed")
        raise self.retry(exc=exc)

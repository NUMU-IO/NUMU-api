"""Expire WhatsApp access whose paid period has ended.

The entitlement check reads ``active_until`` directly, so a lapsed store stops
sending the moment the period ends whether or not this has run. What this adds
is the visible state: the merchant's hub says "expired" and offers the renewal
instead of silently failing, and the admin queue shows who has lapsed.

Hourly, because an expiry that shows up eleven hours late looks like a bug to
the merchant staring at the screen.
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


async def _expire() -> dict:
    from src.application.services.whatsapp_entitlement import expire_lapsed_access
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        stats = await expire_lapsed_access(session)
        await session.commit()
        return stats


@celery_app.task(
    name="tasks.expire_whatsapp_access",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def expire_whatsapp_access_task(self):
    """Flip APPROVED rows past their ``active_until`` to EXPIRED."""
    try:
        stats = _run_async(_expire())
        if stats.get("expired"):
            logger.info("whatsapp_access_expiry_sweep", **stats)
        return stats
    except Exception as exc:
        logger.exception("whatsapp_access_expiry_failed")
        raise self.retry(exc=exc)

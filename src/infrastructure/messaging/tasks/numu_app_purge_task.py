"""Delete the data of NUMU Apps uninstalled more than 30 days ago.

Uninstalling WhatsApp or the Inbox keeps the store's conversations for 30 days
so a reinstall restores them (the uninstall dialog promises exactly that).
After the window they are deleted: customer conversations a merchant no longer
uses should not be kept forever (PDPL 151/2020). See numu_apps.purge_due.

Daily is plenty: the promise is "30 days", not "30 days to the minute".
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


async def _purge() -> dict:
    from src.application.services.numu_apps import purge_due
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        stats = await purge_due(session)
        await session.commit()
        return stats


@celery_app.task(
    name="tasks.purge_uninstalled_numu_apps",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
)
def purge_uninstalled_numu_apps_task(self):
    """Delete conversations of NUMU Apps past their 30-day retention."""
    try:
        stats = _run_async(_purge())
        if stats.get("purged"):
            logger.info("numu_app_purge_sweep", **stats)
        return stats
    except Exception as exc:
        logger.exception("numu_app_purge_failed")
        raise self.retry(exc=exc)

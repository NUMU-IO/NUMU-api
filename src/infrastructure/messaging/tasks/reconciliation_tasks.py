"""Celery task: daily payment reconciliation."""

import asyncio
from datetime import UTC, date, datetime, timedelta

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """Run async code from a synchronous Celery task using a persistent loop."""
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
    return _task_loop.run_until_complete(coro)


async def _reconcile_yesterday() -> dict:
    """Run reconciliation for the previous calendar day (UTC)."""
    from src.application.services.reconciliation_service import ReconciliationService
    from src.infrastructure.database.connection import AsyncSessionLocal

    yesterday: date = (datetime.now(UTC) - timedelta(days=1)).date()

    async with AsyncSessionLocal() as session:
        service = ReconciliationService(session)
        run = await service.run_for_date(yesterday)
        await session.commit()

    return {
        "run_id": str(run.id),
        "date": str(yesterday),
        "status": run.status.value,
        "orders_checked": run.total_orders_checked,
        "mismatches": run.mismatches_found,
    }


@celery_app.task(
    name="tasks.daily_payment_reconciliation",
    bind=True,
    max_retries=2,
    default_retry_delay=300,  # 5 minutes between retries
)
def daily_payment_reconciliation(self) -> dict:
    """Reconcile paid orders against payment transactions for yesterday.

    Scheduled daily at 02:00 UTC (after end-of-day gateway settlement).
    Retries up to 2 times on failure with 5-minute gaps.
    """
    try:
        result = _run_async(_reconcile_yesterday())
        logger.info("reconciliation_task_done", **result)
        return result
    except Exception as exc:
        logger.error("reconciliation_task_failed", error=str(exc))
        raise self.retry(exc=exc)

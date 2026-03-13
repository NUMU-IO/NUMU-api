"""Celery task for webhook retry processing."""

import asyncio

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


@celery_app.task(
    name="tasks.retry_pending_webhook_deliveries", bind=True, max_retries=0
)
def retry_pending_webhook_deliveries(self) -> dict:
    """Pick up all due pending webhook deliveries and fire them.

    Runs every 15 seconds via beat schedule. The shortest retry delay
    is 10 seconds, so 15-second polling provides adequate coverage.
    """
    from src.application.services.webhook_delivery_service import (
        retry_pending_deliveries,
    )

    try:
        count = _run_async(retry_pending_deliveries())
        if count:
            logger.info("webhook_retries_dispatched", count=count)
        return {"processed": count}
    except Exception as exc:
        logger.error("webhook_retry_task_failed", error=str(exc))
        return {"processed": 0, "error": str(exc)}

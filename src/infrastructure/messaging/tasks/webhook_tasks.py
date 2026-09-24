"""Celery task for webhook retry processing."""

import asyncio
from datetime import UTC, datetime

from src.core.logging import get_logger
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

    Runs every 60 seconds via the beat schedule, which is also the real floor
    on the retry ladder: the first two rungs (10s, 30s) land inside one tick.
    The docstring used to claim 15 seconds, which is where the published
    backoff and the actual one parted company.

    Prunes settled delivery logs on the first tick of each hour. Nothing
    pruned them before, so the table grew for the life of the store; doing it
    here costs one extra query an hour and no new beat entry.
    """
    from src.application.services.webhook_delivery_service import (
        purge_old_delivery_logs,
        retry_pending_deliveries,
    )

    try:
        count = _run_async(retry_pending_deliveries())
        if count:
            logger.info("webhook_retries_dispatched", count=count)
        purged = 0
        if datetime.now(UTC).minute == 0:
            purged = _run_async(purge_old_delivery_logs())
            if purged:
                logger.info("webhook_delivery_logs_purged", count=purged)
        return {"processed": count, "purged": purged}
    except Exception as exc:
        logger.error("webhook_retry_task_failed", error=str(exc))
        return {"processed": 0, "error": str(exc)}


@celery_app.task(name="tasks.send_inventory_level_webhook", bind=True, max_retries=2)
def send_inventory_level_task(
    self, store_id: str, product_id: str, variant_id: str
) -> None:
    """``inventory.level_changed`` when its debounce window closes."""
    from src.infrastructure.events.handlers.webhook_handler import (
        send_inventory_level,
    )

    try:
        _run_async(send_inventory_level(store_id, product_id, variant_id))
    except Exception as exc:
        logger.exception("inventory_level_webhook_failed")
        raise self.retry(exc=exc, countdown=60)

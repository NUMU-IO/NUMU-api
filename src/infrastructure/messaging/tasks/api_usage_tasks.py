"""Celery task that persists public-API usage aggregates from Redis."""

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.messaging.tasks.webhook_tasks import _run_async

logger = get_logger(__name__)


@celery_app.task(name="tasks.flush_api_usage", bind=True, max_retries=0)
def flush_api_usage(self) -> dict:
    """Copy the day aggregates Redis collected into ``api_usage_daily``.

    Every 5 minutes: the history survives Redis key expiry, and a Redis
    restart loses at most one interval. Safe to overlap or re-run (see
    ``api_limits.flush_usage``).
    """
    from src.application.services.api_limits import flush_usage
    from src.infrastructure.database.connection import AsyncSessionLocal

    async def _flush() -> int:
        async with AsyncSessionLocal() as session:
            return await flush_usage(session)

    try:
        return {"rows": _run_async(_flush())}
    except Exception as exc:
        logger.error("api_usage_flush_failed", error=str(exc))
        return {"rows": 0, "error": str(exc)}

"""Drop Partner App API log entries older than 14 days.

The per-app logs are capped Redis lists (src/api/middleware/token_activity.py);
a quiet app's list would otherwise keep two-month-old requests. Hourly
counters expire on their own. Daily is plenty.
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


async def _trim() -> int:
    from src.api.middleware.token_activity import trim_app_logs
    from src.infrastructure.cache.redis_cache import RedisCacheService

    cache = RedisCacheService()
    try:
        return await trim_app_logs(await cache._get_client())
    finally:
        await cache.close()


@celery_app.task(name="tasks.trim_app_api_logs", bind=True, max_retries=2)
def trim_app_api_logs_task(self):
    """Trim every Partner App's API log to its retention window."""
    try:
        dropped = _run_async(_trim())
        if dropped:
            logger.info("app_api_logs_trimmed", dropped=dropped)
        return {"dropped": dropped}
    except Exception as exc:
        logger.exception("app_api_log_trim_failed")
        raise self.retry(exc=exc, countdown=600)

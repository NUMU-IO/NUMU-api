"""Beat scheduler heartbeat (Phase 5.8).

Tiny periodic task that writes the current unix timestamp to Redis
under `celery_beat_last_run`. The /health/detailed Celery check reads
that key to detect a stuck beat scheduler — workers might be up but
the scheduler process can hang silently (lost broker connection,
exhausted file descriptors, OOM-killed and restarting).

We write with a generous TTL (10 minutes) so a beat that misses a
single tick doesn't immediately register as stale; the staleness
threshold lives in the health check (~120s).

Why a separate task instead of piggybacking on an existing periodic
task: a domain-bound task (smart-collection sweep, back-in-stock,
etc.) that fails would also stop heartbeat updates, masking a beat
problem as a domain problem. Keeping the heartbeat its own no-op
task isolates the signal.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


@celery_app.task(name="tasks.beat_heartbeat")
def beat_heartbeat_task() -> dict[str, int]:
    """Write a unix-ts marker so /health/detailed can detect a
    stuck beat scheduler."""
    try:
        from src.infrastructure.cache.redis_cache import RedisCacheService

        async def _write() -> int:
            cache = RedisCacheService()
            ts = int(time.time())
            await cache.set("celery_beat_last_run", str(ts), expire=600)
            return ts

        ts = _run_async(_write())
        return {"ts": ts}
    except Exception as exc:
        # Don't raise — we don't want this to retry. A failed
        # heartbeat surfaces as a stale value in /health/detailed,
        # which is exactly the signal we want.
        logger.warning("beat_heartbeat_failed: %s", exc)
        return {"ts": 0}

"""Redis SET-NX wrapper for one-shot dedupe of async ingest events.

Layer 2 of the Step 09 idempotency stack (client UUID → Redis SET NX →
DB UNIQUE). The handler calls :meth:`IdempotencyKeys.claim` before
pushing to Celery; if the key was already present, the event was
already enqueued and we skip the push.

Redis outages are non-fatal — ``claim`` degrades open (returns True
so the caller proceeds to enqueue), logs a warning, and the DB
``UNIQUE`` constraint catches the eventual duplicate at the worker.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from redis.exceptions import RedisError

if TYPE_CHECKING:  # pragma: no cover
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class IdempotencyKeys:
    """``SET NX EX`` wrapper for one-shot dedupe."""

    DEFAULT_TTL = 86_400  # 24h — funnel events arriving > 24h late are useless

    def __init__(
        self,
        redis: Redis | Any,
        *,
        ttl_seconds: int = DEFAULT_TTL,
        namespace: str = "idempotent",
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    async def claim(self, key: str) -> bool:
        """Try to claim ``key``. Returns True iff the key was new.

        Returns False only when Redis confirms the key already existed
        (the operation was previously enqueued). On any Redis error
        the helper degrades open — returns True so the caller still
        enqueues; the DB UNIQUE constraint is the final safety net.
        """
        try:
            result = await self._redis.set(self._key(key), "1", nx=True, ex=self._ttl)
        except RedisError as exc:
            logger.warning(
                "idempotency_keys.claim redis error (key=%s): %s — degrading open",
                key,
                exc,
            )
            return True
        # redis-py returns True on success and None when nx prevented the
        # set. Some clients return False instead of None.
        return result is True or result == b"OK" or result == "OK"


# ----------------------------------------------------------------------
# Singleton + factory
# ----------------------------------------------------------------------


_idempotency_keys_instance: IdempotencyKeys | None = None


def get_idempotency_keys() -> IdempotencyKeys:
    """Process-wide singleton bound to the configured Redis URL."""
    global _idempotency_keys_instance
    if _idempotency_keys_instance is None:
        import redis.asyncio as redis_async

        from src.config import settings

        client = redis_async.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
        _idempotency_keys_instance = IdempotencyKeys(
            redis=client,
            ttl_seconds=settings.analytics_idempotency_ttl_seconds,
        )
    return _idempotency_keys_instance


def reset_idempotency_keys_singleton() -> None:
    """Test hook — drop the singleton so a fresh client is constructed."""
    global _idempotency_keys_instance
    _idempotency_keys_instance = None

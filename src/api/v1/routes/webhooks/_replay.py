"""Replay keys for gateway webhooks, released when processing fails."""

from collections.abc import AsyncIterator

from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = get_logger(__name__)


class ReplayKeys:
    """The replay keys one webhook call has taken."""

    def __init__(self) -> None:
        self.taken: list[tuple[RedisCacheService, str]] = []

    async def take(self, cache: RedisCacheService, key: str, ttl: int) -> bool:
        """Take ``key``; False when another call already holds it (duplicate)."""
        if not await cache.set_if_absent(key, "1", expire=ttl):
            return False
        self.taken.append((cache, key))
        return True


async def replay_keys() -> AsyncIterator[ReplayKeys]:
    """FastAPI dependency: release every taken key if the endpoint raises.

    A key kept for a call that then failed turns the gateway's retry into a
    "duplicate", and a paid order is never recorded. The error still answers
    5xx so the gateway retries.
    """
    keys = ReplayKeys()
    try:
        yield keys
    except Exception:
        for cache, key in keys.taken:
            try:
                released = await cache.delete(key)
            except Exception:  # noqa: BLE001 - never mask the original error
                released = False
            if not released:
                # The retry reads as a duplicate until the key expires; an
                # operator can delete it by hand.
                logger.error("webhook_nonce_release_failed", nonce_key=key)
        raise

"""Refresh token blacklist — detect and block token reuse.

When a refresh token is consumed, its jti is blacklisted in Redis with
a TTL matching the token's remaining lifetime. If the same jti is used
again, it indicates the token was stolen and we reject it.

Graceful degradation: if Redis is unavailable, tokens are accepted
(failover is preferable to full outage).
"""

import time

from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = get_logger(__name__)


class RefreshTokenBlacklistService:
    """Track consumed refresh token JTIs to detect reuse."""

    def __init__(self, cache: RedisCacheService) -> None:
        self._cache = cache

    @staticmethod
    def _key(jti: str) -> str:
        return f"refresh_jti_used:{jti}"

    async def is_used(self, jti: str) -> bool:
        """Return True if this jti has already been consumed."""
        try:
            return await self._cache.exists(self._key(jti))
        except Exception:
            logger.debug("refresh_blacklist_check_failed", jti=jti)
            return False

    async def mark_used(self, jti: str, token_exp: int) -> None:
        """Blacklist jti until the token would have naturally expired."""
        try:
            ttl = max(token_exp - int(time.time()), 60)
            await self._cache.set(self._key(jti), "1", expire=ttl)
        except Exception:
            logger.debug("refresh_blacklist_write_failed", jti=jti)

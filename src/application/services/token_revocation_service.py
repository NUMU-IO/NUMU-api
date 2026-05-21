"""Token revocation service — invalidates all sessions on password change.

When a user changes their password, we record the timestamp in Redis.
Any token issued *before* that timestamp is considered revoked.

The revocation record expires after the refresh token lifetime so we
don't accumulate stale keys forever.
"""

from uuid import UUID

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = get_logger(__name__)

# TTL matches refresh token lifetime — no token older than this is valid anyway
_REVOCATION_TTL_SECONDS = settings.refresh_token_expire_days * 86400


class TokenRevocationService:
    """Redis-backed per-user token revocation keyed on password-change timestamp."""

    def __init__(self, cache: RedisCacheService) -> None:
        self._cache = cache

    @staticmethod
    def _key(user_id: UUID) -> str:
        return f"token_revoked_at:{user_id}"

    async def revoke_all(self, user_id: UUID, revoked_at: int) -> None:
        """Mark all tokens issued before *revoked_at* (unix timestamp) as revoked."""
        try:
            await self._cache.set(
                self._key(user_id),
                revoked_at,
                expire=_REVOCATION_TTL_SECONDS,
            )
            logger.info("tokens_revoked", user_id=str(user_id), revoked_at=revoked_at)
        except Exception:
            logger.warning("token_revocation_redis_unavailable", user_id=str(user_id))

    async def is_revoked(self, user_id: UUID, token_iat: int) -> bool:
        """Return True if the token (identified by its iat) has been revoked.

        Degrades gracefully: if Redis is down, tokens are considered valid.
        """
        try:
            revoked_at = await self._cache.get(self._key(user_id))
            if revoked_at is None:
                return False
            return token_iat < int(revoked_at)
        except Exception:
            logger.debug("token_revocation_check_failed", user_id=str(user_id))
            return False

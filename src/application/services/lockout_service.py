"""Account lockout service — Redis-backed brute force protection.

Tracks failed login attempts per email address and enforces
temporary lockouts with exponential backoff.

Lockout schedule (starting at attempt 5):
  attempt 5 → 60s
  attempt 6 → 120s
  attempt 7 → 240s
  attempt 8 → 480s
  attempt 9+ → 900s (15 min, capped)
"""

from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = get_logger(__name__)

_MAX_ATTEMPTS = 5
_BASE_LOCKOUT_SECONDS = 60
_MAX_LOCKOUT_SECONDS = 900  # 15 minutes
_ATTEMPT_WINDOW_SECONDS = 600  # track attempts for 10 minutes


class AccountLockoutService:
    """Redis-backed account lockout with exponential backoff."""

    def __init__(self, cache: RedisCacheService) -> None:
        self._cache = cache

    # ------------------------------------------------------------------ #
    # Key helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _attempts_key(email: str) -> str:
        return f"lockout:attempts:{email.lower()}"

    @staticmethod
    def _locked_key(email: str) -> str:
        return f"lockout:locked:{email.lower()}"

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    async def check_locked(self, email: str) -> tuple[bool, int]:
        """Return (is_locked, retry_after_seconds).

        If Redis is unavailable, returns (False, 0) to avoid blocking logins.
        """
        try:
            client = await self._cache._get_client()
            ttl = await client.ttl(self._locked_key(email))
            if ttl > 0:
                return True, ttl
            return False, 0
        except Exception:
            logger.debug("lockout_redis_unavailable_check", email=email)
            return False, 0

    async def record_failure(self, email: str) -> None:
        """Increment failure counter and apply lockout if threshold reached."""
        try:
            client = await self._cache._get_client()
            key = self._attempts_key(email)
            attempts = await client.incr(key)
            if attempts == 1:
                await client.expire(key, _ATTEMPT_WINDOW_SECONDS)

            logger.warning(
                "auth_failed_attempt",
                email=email,
                attempts=attempts,
            )

            if attempts >= _MAX_ATTEMPTS:
                extra = attempts - _MAX_ATTEMPTS  # 0 on 5th attempt
                lockout = min(
                    _BASE_LOCKOUT_SECONDS * (2**extra),
                    _MAX_LOCKOUT_SECONDS,
                )
                await client.set(self._locked_key(email), "1", ex=lockout)
                logger.warning(
                    "auth_account_locked",
                    email=email,
                    attempts=attempts,
                    lockout_seconds=lockout,
                )
        except Exception:
            logger.debug("lockout_redis_unavailable_record", email=email)

    async def clear(self, email: str) -> None:
        """Clear all failure state after a successful login."""
        try:
            client = await self._cache._get_client()
            await client.delete(
                self._attempts_key(email),
                self._locked_key(email),
            )
        except Exception:
            logger.debug("lockout_redis_unavailable_clear", email=email)

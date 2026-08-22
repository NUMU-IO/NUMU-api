"""Account lockout service — Redis-backed brute force protection.

Tracks failed login attempts per email address and enforces
temporary lockouts with exponential backoff.

Lockout schedule (starting at attempt 5):
  attempt 5 → 60s
  attempt 6 → 120s
  attempt 7 → 240s
  attempt 8 → 480s
  attempt 9+ → 900s (15 min, capped)

Redis outage: the old behaviour was fail-OPEN with a debug log — brute
force became unlimited exactly when the cache was down. Now each process
keeps a small in-memory fallback (same thresholds, per-worker) so an
outage degrades to "per-process lockout" instead of "no lockout", and
the outage itself is logged at error level once per minute.
"""

import time
from collections import OrderedDict

from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = get_logger(__name__)

_MAX_ATTEMPTS = 5
_BASE_LOCKOUT_SECONDS = 60
_MAX_LOCKOUT_SECONDS = 900  # 15 minutes
_ATTEMPT_WINDOW_SECONDS = 600  # track attempts for 10 minutes

_FALLBACK_MAX_KEYS = 5_000
_OUTAGE_LOG_INTERVAL_S = 60.0


class _MemoryFallback:
    """Tiny LRU of {key: (value, expires_at)} used only while Redis is down."""

    def __init__(self) -> None:
        self._data: OrderedDict[str, tuple[int, float]] = OrderedDict()

    def _purge(self) -> None:
        now = time.monotonic()
        for k in [k for k, (_, exp) in self._data.items() if exp <= now]:
            self._data.pop(k, None)
        while len(self._data) > _FALLBACK_MAX_KEYS:
            self._data.popitem(last=False)

    def ttl(self, key: str) -> int:
        self._purge()
        entry = self._data.get(key)
        if not entry:
            return -2
        return max(0, int(entry[1] - time.monotonic()))

    def incr(self, key: str, window: int) -> int:
        self._purge()
        value, exp = self._data.get(key, (0, 0.0))
        if exp <= time.monotonic():
            value, exp = 0, time.monotonic() + window
        value += 1
        self._data[key] = (value, exp)
        self._data.move_to_end(key)
        return value

    def set(self, key: str, ttl: int) -> None:
        self._purge()
        self._data[key] = (1, time.monotonic() + ttl)

    def delete(self, *keys: str) -> None:
        for k in keys:
            self._data.pop(k, None)


_fallback = _MemoryFallback()
_last_outage_log = 0.0


def _log_outage(where: str, **ctx: object) -> None:
    global _last_outage_log
    now = time.monotonic()
    if now - _last_outage_log >= _OUTAGE_LOG_INTERVAL_S:
        _last_outage_log = now
        logger.error("lockout_redis_unavailable", where=where, **ctx)


class AccountLockoutService:
    """Redis-backed account lockout with exponential backoff + memory fallback."""

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

    @staticmethod
    def _lockout_for(attempts: int) -> int:
        extra = attempts - _MAX_ATTEMPTS  # 0 on 5th attempt
        return min(_BASE_LOCKOUT_SECONDS * (2**extra), _MAX_LOCKOUT_SECONDS)

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    async def check_locked(self, email: str) -> tuple[bool, int]:
        """Return (is_locked, retry_after_seconds)."""
        try:
            client = await self._cache._get_client()
            ttl = await client.ttl(self._locked_key(email))
        except Exception:
            _log_outage("check", email=email)
            ttl = _fallback.ttl(self._locked_key(email))
        if ttl > 0:
            return True, ttl
        return False, 0

    async def record_failure(self, email: str) -> None:
        """Increment failure counter and apply lockout if threshold reached."""
        key = self._attempts_key(email)
        try:
            client = await self._cache._get_client()
            attempts = await client.incr(key)
            if attempts == 1:
                await client.expire(key, _ATTEMPT_WINDOW_SECONDS)
            setter = client
        except Exception:
            _log_outage("record", email=email)
            attempts = _fallback.incr(key, _ATTEMPT_WINDOW_SECONDS)
            setter = None

        logger.warning("auth_failed_attempt", email=email, attempts=attempts)

        if attempts >= _MAX_ATTEMPTS:
            lockout = self._lockout_for(attempts)
            if setter is not None:
                try:
                    await setter.set(self._locked_key(email), "1", ex=lockout)
                except Exception:
                    _log_outage("lock", email=email)
                    _fallback.set(self._locked_key(email), lockout)
            else:
                _fallback.set(self._locked_key(email), lockout)
            logger.warning(
                "auth_account_locked",
                email=email,
                attempts=attempts,
                lockout_seconds=lockout,
            )

    async def clear(self, email: str) -> None:
        """Clear all failure state after a successful login."""
        keys = (self._attempts_key(email), self._locked_key(email))
        _fallback.delete(*keys)
        try:
            client = await self._cache._get_client()
            await client.delete(*keys)
        except Exception:
            _log_outage("clear", email=email)


class EmailActionThrottle:
    """Per-EMAIL budget for self-service account actions.

    The per-IP limiter can't see the email, and the old "per-user" layer
    hashed the bearer token — which anonymous forgot/reset-password calls
    don't have — so one address could be hammered from many IPs. Keyed by
    normalised email; same Redis-or-memory fallback as the lockout.
    """

    def __init__(self, cache: RedisCacheService) -> None:
        self._cache = cache

    @staticmethod
    def _key(action: str, email: str) -> str:
        return f"email_throttle:{action}:{email.strip().lower()}"

    async def hit(
        self, action: str, email: str, *, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """Record one attempt. Returns (allowed, retry_after_seconds)."""
        key = self._key(action, email)
        try:
            client = await self._cache._get_client()
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, window_seconds)
            ttl = await client.ttl(key) if count > limit else 0
        except Exception:
            _log_outage("email_throttle", action=action)
            count = _fallback.incr(key, window_seconds)
            ttl = _fallback.ttl(key) if count > limit else 0
        if count > limit:
            return False, max(int(ttl), 1)
        return True, 0

"""Refresh token blacklist — detect and block token reuse.

When a refresh token is consumed, its jti is blacklisted in Redis with
a TTL matching the token's remaining lifetime. If the same jti is used
again, it indicates the token was stolen and we reject it.

Graceful degradation: if Redis is unavailable, tokens are accepted
(failover is preferable to full outage).
"""

import json
import time

from src.core.logging import get_logger
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
        """Return True if this jti has already been consumed.

        Fail-open on a Redis outage (a hiccup must not log everyone out),
        but LOUDLY: error-level, so the outage is an alert, not a secret.
        """
        try:
            return await self._cache.exists(self._key(jti))
        except Exception as exc:
            logger.error(
                "refresh_blacklist_unavailable_fail_open",
                jti=jti,
                error=str(exc),
            )
            return False

    # ── Token families ─────────────────────────────────────────────────
    # Every login mints a refresh token with a fresh family_id that is
    # carried across rotations. Reuse of a consumed jti AFTER the grace
    # window means the token was copied; revoking the family kills the
    # attacker's rotated copy AND the victim's, forcing one clean login.

    @staticmethod
    def _family_key(family_id: str) -> str:
        return f"refresh_family_revoked:{family_id}"

    async def revoke_family(self, family_id: str, ttl: int) -> None:
        try:
            await self._cache.set(self._family_key(family_id), "1", expire=max(ttl, 60))
        except Exception as exc:
            logger.error(
                "refresh_family_revoke_failed", family_id=family_id, error=str(exc)
            )

    async def is_family_revoked(self, family_id: str) -> bool:
        try:
            return await self._cache.exists(self._family_key(family_id))
        except Exception as exc:
            logger.error(
                "refresh_family_check_unavailable_fail_open",
                family_id=family_id,
                error=str(exc),
            )
            return False

    async def mark_used(self, jti: str, token_exp: int) -> None:
        """Blacklist jti until the token would have naturally expired."""
        try:
            ttl = max(token_exp - int(time.time()), 60)
            await self._cache.set(self._key(jti), "1", expire=ttl)
        except Exception:
            logger.debug("refresh_blacklist_write_failed", jti=jti)

    # ── Rotation grace ────────────────────────────────────────────────
    # The pair minted when a jti was consumed, kept briefly so a second
    # presenter of the SAME jti (another tab that raced the refresh) gets
    # the same pair instead of a 401 that logs the merchant out.

    @staticmethod
    def _rotation_key(jti: str) -> str:
        return f"refresh_rotated:{jti}"

    async def remember_rotation(
        self, jti: str, access_token: str, refresh_token: str, ttl: int
    ) -> None:
        if ttl <= 0:
            return
        try:
            await self._cache.set(
                self._rotation_key(jti),
                json.dumps({"access": access_token, "refresh": refresh_token}),
                expire=ttl,
            )
        except Exception:
            logger.debug("refresh_rotation_write_failed", jti=jti)

    async def get_rotation(self, jti: str) -> tuple[str, str] | None:
        try:
            raw = await self._cache.get(self._rotation_key(jti))
        except Exception:
            logger.debug("refresh_rotation_read_failed", jti=jti)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            return data["access"], data["refresh"]
        except (ValueError, KeyError, AttributeError):
            return None

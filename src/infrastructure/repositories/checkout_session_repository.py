"""Redis-backed repository for CheckoutSession (FR-007b).

30-minute TTL. Reuses the same Redis client pattern as RedisCartRepository.
Keys: ``checkout_session:{token}`` → JSON.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import redis.asyncio as redis

from src.config import settings
from src.core.entities.checkout_session import CheckoutSession


class CheckoutSessionRepository:
    """Redis-backed repository for checkout-session tokens."""

    DEFAULT_TTL_SECONDS: int = 30 * 60  # 30 minutes
    KEY_PREFIX: str = "checkout_session"

    def __init__(
        self,
        redis_url: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self.redis_url = redis_url or settings.redis_url
        self.ttl_seconds = ttl_seconds or self.DEFAULT_TTL_SECONDS
        self._client: redis.Redis | None = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    def _key(self, token: UUID) -> str:
        return f"{self.KEY_PREFIX}:{token}"

    async def create(
        self,
        *,
        cart_session_id: str,
        store_id: UUID,
        phone: str,
    ) -> CheckoutSession:
        """Issue a fresh token bound to (cart, store, phone).

        Phone MUST already be canonicalized to E.164 by the caller.
        """
        client = await self._get_client()
        token = uuid4()
        now = datetime.now(UTC)
        session = CheckoutSession(
            token=token,
            cart_session_id=cart_session_id,
            store_id=store_id,
            phone=phone,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        await client.set(
            self._key(token),
            session.model_dump_json(),
            ex=self.ttl_seconds,
        )
        return session

    async def get(self, token: UUID) -> CheckoutSession | None:
        """Resolve a token to its session. Returns None if missing/expired."""
        client = await self._get_client()
        raw = await client.get(self._key(token))
        if raw is None:
            return None
        try:
            return CheckoutSession.model_validate_json(raw)
        except Exception:
            # Corrupt row — delete and treat as missing rather than blow up.
            await client.delete(self._key(token))
            return None

    async def delete(self, token: UUID) -> None:
        client = await self._get_client()
        await client.delete(self._key(token))

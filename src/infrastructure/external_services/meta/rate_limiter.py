"""Redis-based token bucket rate limiter for Meta API calls."""

import time

import redis.asyncio as redis

from src.config.logging_config import get_logger

logger = get_logger(__name__)


class MetaRateLimiter:
    """Token bucket rate limiter using Redis.

    Limits API calls per tenant/channel to respect Meta's rate limits:
    - Instagram: 200 calls/hour/user
    - Facebook: 200 calls/hour/user
    - WhatsApp: 1000 msgs/s/phone-number
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        channel: str,
        tenant_id: str,
        max_tokens: int = 200,
        refill_rate: float = 200 / 3600,
    ):
        self.redis = redis_client
        self.channel = channel
        self.tenant_id = tenant_id
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.key = f"rate_limit:meta:{tenant_id}:{channel}"

    async def acquire(self, tokens: int = 1, block: bool = True) -> bool:
        """Acquire tokens from the bucket.

        Args:
            tokens: Number of tokens to acquire
            block: If True, wait for tokens; if False, return immediately

        Returns:
            True if tokens acquired, False otherwise
        """
        now = time.time()

        current = await self.redis.get(self.key)
        if current:
            tokens_available, last_refill = map(float, current.split(":"))
            tokens_to_add = (now - last_refill) * self.refill_rate
            tokens_available = min(self.max_tokens, tokens_available + tokens_to_add)
        else:
            tokens_available = self.max_tokens
            last_refill = now

        if tokens_available >= tokens:
            tokens_available -= tokens
            await self.redis.set(
                self.key,
                f"{tokens_available}:{now}",
                ex=3600,
            )
            return True

        if not block:
            return False

        wait_time = (tokens - tokens_available) / self.refill_rate
        logger.warning(
            "rate_limit_wait",
            channel=self.channel,
            tenant_id=self.tenant_id,
            wait_seconds=wait_time,
        )
        time.sleep(wait_time)
        return await self.acquire(tokens, block=False)

    async def get_remaining(self) -> int:
        """Get remaining tokens in bucket."""
        now = time.time()
        current = await self.redis.get(self.key)

        if not current:
            return self.max_tokens

        tokens_available, last_refill = map(float, current.split(":"))
        tokens_to_add = (now - last_refill) * self.refill_rate
        return int(max(0, min(self.max_tokens, tokens_available + tokens_to_add)))

    async def reset(self) -> None:
        """Reset the rate limiter."""
        await self.redis.delete(self.key)
        logger.info("rate_limit_reset", channel=self.channel, tenant_id=self.tenant_id)


class RateLimiterRegistry:
    """Registry for managing rate limiters per channel."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self._limiters: dict[str, MetaRateLimiter] = {}

    def get_limiter(
        self,
        channel: str,
        tenant_id: str,
        max_tokens: int = 200,
    ) -> MetaRateLimiter:
        """Get or create a rate limiter for a channel."""
        key = f"{tenant_id}:{channel}"
        if key not in self._limiters:
            self._limiters[key] = MetaRateLimiter(
                self.redis,
                channel,
                tenant_id,
                max_tokens,
            )
        return self._limiters[key]

    async def apply(self, channel: str, tenant_id: str, tokens: int = 1) -> bool:
        """Apply rate limiting."""
        limiter = self.get_limiter(channel, tenant_id)
        return await limiter.acquire(tokens)

    async def on_429(self, channel: str, tenant_id: str) -> None:
        """Handle 429 response - back off."""
        limiter = self.get_limiter(channel, tenant_id)
        remaining = await limiter.get_remaining()
        logger.warning(
            "rate_limit_429",
            channel=channel,
            tenant_id=tenant_id,
            remaining=remaining,
        )
        await limiter.reset()
        time.sleep(30)

"""Redis pub/sub for realtime messaging."""

from typing import Any

import redis.asyncio as redis

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)


class RealtimePublisher:
    """Publisher for realtime inbox events via Redis pub/sub."""

    def __init__(self, redis_client: redis.Redis | None = None):
        self.redis = redis_client or redis.from_url(settings.redis_url)

    async def publish(
        self,
        channel: str,
        event: dict[str, Any],
    ) -> None:
        """Publish an event to a channel.

        Args:
            channel: Channel name (e.g., 'inbox:{tenant_id}:{store_id}')
            event: Event payload
        """
        import json

        await self.redis.publish(channel, json.dumps(event))
        logger.debug("realtime_publish", channel=channel, event_type=event.get("type"))

    async def subscribe(
        self,
        channel: str,
    ) -> redis.client.PubSub:
        """Subscribe to a channel.

        Args:
            channel: Channel name

        Returns:
            PubSub instance
        """
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        return pubsub

    async def unsubscribe(
        self,
        channel: str,
        pubsub: redis.client.PubSub,
    ) -> None:
        """Unsubscribe from a channel and close the pubsub connection."""
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()

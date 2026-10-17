"""Redis pub/sub for realtime messaging."""

from typing import Any

import anyio
import redis.asyncio as redis

from src.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

_CLOSE_TIMEOUT_S = 5


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
        """Unsubscribe from a channel and close the pubsub connection.

        Shielded: callers run this in the ``finally`` of an SSE/WebSocket
        stream the client just left, inside a cancelled anyio scope. Without
        the shield the cancellation interrupts the close, the connection stays
        open, and Python warns "coroutine 'AbstractConnection.disconnect' was
        never awaited".
        """
        with anyio.move_on_after(_CLOSE_TIMEOUT_S, shield=True):
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def aclose(self) -> None:
        """Close this publisher's Redis pool (shielded, see ``unsubscribe``)."""
        with anyio.move_on_after(_CLOSE_TIMEOUT_S, shield=True):
            await self.redis.aclose()

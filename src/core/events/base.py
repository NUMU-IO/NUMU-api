"""Base event infrastructure for domain-driven event dispatch.

Provides a simple in-process event bus that:
- Registers async handler coroutines per event type
- Dispatches events to all registered handlers concurrently
- Isolates handler failures so one failing handler never blocks others
- Runs handlers as fire-and-forget asyncio tasks (non-blocking)
"""

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.config.logging_config import get_logger

logger = get_logger(__name__)

# Type alias for async event handler functions
EventHandler = Callable[["DomainEvent"], Coroutine[Any, Any, None]]


class DomainEvent(BaseModel):
    """Base class for all domain events."""

    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.event_type:
            object.__setattr__(self, "event_type", self.__class__.__name__)


class EventBus:
    """In-process async event bus.

    Handlers are registered per event type and run concurrently as
    fire-and-forget tasks when an event is published. Handler failures
    are logged but never propagate to the publisher.

    Usage:
        bus = EventBus()
        bus.subscribe(OrderStatusChangedEvent, email_handler)
        bus.subscribe(OrderStatusChangedEvent, activity_log_handler)
        await bus.publish(event)  # schedules handlers, returns immediately
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_class: type[DomainEvent], handler: EventHandler) -> None:
        """Register a handler for an event type."""
        event_type = event_class.__name__
        self._handlers[event_type].append(handler)
        logger.debug(
            "event_handler_registered", event_type=event_type, handler=handler.__name__
        )

    def publish(self, event: DomainEvent) -> None:
        """Publish an event — schedules all handlers as background tasks.

        Non-blocking: returns immediately after scheduling. Each handler
        runs in its own asyncio task with isolated error handling.
        """
        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            return

        for handler in handlers:
            asyncio.create_task(
                self._safe_invoke(handler, event),
                name=f"event:{event.event_type}:{handler.__name__}",
            )

        logger.info(
            "event_published",
            event_type=event.event_type,
            event_id=str(event.event_id),
            handler_count=len(handlers),
        )

    async def _safe_invoke(self, handler: EventHandler, event: DomainEvent) -> None:
        """Invoke a handler with error isolation."""
        try:
            await handler(event)
        except Exception as exc:
            logger.error(
                "event_handler_failed",
                event_type=event.event_type,
                event_id=str(event.event_id),
                handler=handler.__name__,
                error=str(exc),
            )

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


def _schedule_immediately(
    bus: "EventBus", event: "DomainEvent", handlers: list[EventHandler]
) -> None:
    """Default scheduler: run every handler now as a fire-and-forget task.

    This is the historical behaviour. The infrastructure layer may swap in a
    scheduler that defers dispatch until the active DB transaction commits
    (see ``infrastructure.events.deferred_dispatch``).
    """
    for handler in handlers:
        asyncio.create_task(
            bus._safe_invoke(handler, event),
            name=f"event:{event.event_type}:{handler.__name__}",
        )


class EventBus:
    """In-process async event bus.

    Handlers are registered per event type and run concurrently as
    fire-and-forget tasks when an event is published. Handler failures
    are logged but never propagate to the publisher.

    Dispatch timing is governed by ``self.scheduler``. By default handlers
    run immediately; the infrastructure layer can replace it with a
    commit-deferred scheduler so handlers that open their own session never
    race the writer's transaction.

    Usage:
        bus = EventBus()
        bus.subscribe(OrderStatusChangedEvent, email_handler)
        bus.subscribe(OrderStatusChangedEvent, activity_log_handler)
        bus.publish(event)  # schedules handlers, returns immediately
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        # Pluggable dispatch strategy: (bus, event, handlers) -> None.
        # Default fires immediately; infrastructure may install a
        # commit-deferred scheduler.
        self.scheduler: Callable[[EventBus, DomainEvent, list[EventHandler]], None] = (
            _schedule_immediately
        )

    def subscribe(self, event_class: type[DomainEvent], handler: EventHandler) -> None:
        """Register a handler for an event type."""
        event_type = event_class.__name__
        self._handlers[event_type].append(handler)
        logger.debug(
            "event_handler_registered", event_type=event_type, handler=handler.__name__
        )

    def publish(self, event: DomainEvent) -> None:
        """Publish an event — hands all handlers to the active scheduler.

        Non-blocking: returns immediately. With the default scheduler each
        handler runs right away in its own asyncio task; with a deferred
        scheduler dispatch is held until the request transaction commits.
        Handler errors are always isolated and never propagate.
        """
        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            return

        self.scheduler(self, event, list(handlers))

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

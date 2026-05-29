"""Defer domain-event dispatch until the active request transaction commits.

Event handlers run as fire-and-forget tasks that open their *own* database
session. If they run before the request transaction commits, they cannot see
the rows it just wrote — e.g. ``handle_order_created_activity`` inserts an
``order_activities`` row whose ``order_id`` FK references an order that is
flushed but not yet committed, raising ``ForeignKeyViolationError`` and
silently dropping the activity.

This scheduler buffers handler dispatch on the request session and flushes it
from an ``after_commit`` hook, so handlers only run once the data is durable
and visible. On rollback the buffer is discarded, so events never fire for
work that did not persist (e.g. an order-created WhatsApp message for an order
whose transaction rolled back).

It falls back to immediate dispatch when there is no request-scoped session
(Celery tasks, handlers' own sessions, tests), preserving the previous
behaviour in those contexts.
"""

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session as SyncSession

from src.core.events.base import (
    DomainEvent,
    EventBus,
    EventHandler,
    _schedule_immediately,
)
from src.infrastructure.database.connection import get_current_session

_BUFFER_KEY = "_deferred_events"
_LISTENER_FLAG = "_deferred_listener_installed"


def deferred_scheduler(
    bus: EventBus, event: DomainEvent, handlers: list[EventHandler]
) -> None:
    """Buffer dispatch until the request session commits; else dispatch now."""
    session = get_current_session()
    if session is None or not session.in_transaction():
        _schedule_immediately(bus, event, handlers)
        return

    sync_session = session.sync_session
    buffer = sync_session.info.setdefault(_BUFFER_KEY, [])
    buffer.append((bus, event, handlers))

    if not sync_session.info.get(_LISTENER_FLAG):
        sync_session.info[_LISTENER_FLAG] = True
        sa_event.listen(sync_session, "after_commit", _on_commit)
        sa_event.listen(sync_session, "after_rollback", _on_rollback)
        sa_event.listen(sync_session, "after_soft_rollback", _on_soft_rollback)


def _drain(sync_session: SyncSession) -> list:
    return sync_session.info.pop(_BUFFER_KEY, [])


def _on_commit(sync_session: SyncSession) -> None:
    # Runs inside ``await session.commit()`` with the event loop active, so
    # ``asyncio.create_task`` (in _schedule_immediately) works here.
    for bus, event, handlers in _drain(sync_session):
        _schedule_immediately(bus, event, handlers)


def _on_rollback(sync_session: SyncSession) -> None:
    _drain(sync_session)  # discard — the work did not persist


def _on_soft_rollback(sync_session: SyncSession, previous_transaction) -> None:
    _drain(sync_session)

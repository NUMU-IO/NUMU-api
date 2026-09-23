"""Domain events must dispatch only after the request transaction commits.

Regression for the ``order_activities_order_id_fkey`` violation: handlers run
as fire-and-forget tasks that open their own session, so dispatching them
before the writer's transaction committed meant they could not see the
just-written order. The deferred scheduler holds dispatch until commit and
drops it on rollback, with immediate dispatch when there is no request
session.
"""

import asyncio

import pytest
from sqlalchemy import text

from src.core.events.base import DomainEvent, EventBus
from src.infrastructure.database import connection as conn
from src.infrastructure.events.deferred_dispatch import deferred_scheduler


class _Ping(DomainEvent):
    pass


def _bus_with_handler(calls: list) -> EventBus:
    bus = EventBus()
    bus.scheduler = deferred_scheduler

    async def handler(event: DomainEvent) -> None:
        calls.append(event)

    bus.subscribe(_Ping, handler)
    return bus


@pytest.mark.asyncio
async def test_dispatches_immediately_without_request_session():
    calls: list = []
    bus = _bus_with_handler(calls)

    # No request session in context -> falls back to immediate dispatch.
    bus.publish(_Ping())
    await asyncio.sleep(0.05)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_defers_until_commit(test_session):
    calls: list = []
    bus = _bus_with_handler(calls)

    token = conn._current_session.set(test_session)
    try:
        await test_session.execute(text("SELECT 1"))  # open the transaction
        assert test_session.in_transaction()

        bus.publish(_Ping())
        await asyncio.sleep(0.05)
        assert calls == [], "handler must not run before commit"

        await test_session.commit()
        await asyncio.sleep(0.05)
        assert len(calls) == 1, "handler must run after commit"
    finally:
        conn._current_session.reset(token)


@pytest.mark.asyncio
async def test_rollback_discards_events(test_session):
    calls: list = []
    bus = _bus_with_handler(calls)

    token = conn._current_session.set(test_session)
    try:
        await test_session.execute(text("SELECT 1"))
        bus.publish(_Ping())

        await test_session.rollback()
        await asyncio.sleep(0.05)
        assert calls == [], "events for a rolled-back tx must be dropped"
    finally:
        conn._current_session.reset(token)


@pytest.mark.asyncio
async def test_admin_session_is_registered_for_deferred_dispatch(monkeypatch):
    # Payment webhooks run on the admin session. Their events must wait for
    # its commit, or handlers save the pre-payment order over the paid one.
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.close = AsyncMock()

    @asynccontextmanager
    async def fake_session_local():
        yield session

    monkeypatch.setattr(conn, "AsyncSessionLocal", fake_session_local)

    gen = conn.get_admin_db_session()
    assert await gen.__anext__() is session
    assert conn.get_current_session() is session
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert conn.get_current_session() is None

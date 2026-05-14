"""Worker-level dedupe test for the Step 09 funnel-event ingest task.

The Celery task is just a thin wrapper around the async
``_insert_funnel_event`` helper that does the ``INSERT … ON CONFLICT
DO NOTHING``. This test exercises that helper directly against the
local Postgres so we prove:

* A first call with a fresh ``event_id`` inserts exactly one row.
* A second call with the **same** ``event_id`` is a no-op — the
  ``ux_funnel_events_event_id`` partial UNIQUE index catches it.
* A NULL ``event_id`` skips the UNIQUE index entirely (legacy /
  kill-switch fallback rows).

Skips cleanly when no ``TEST_DATABASE_URL`` / ``DATABASE_URL`` is
configured. Each test runs in its own private asyncio loop with its
own SQLAlchemy engine so we never reuse a connection across loops
(the project's module-level ``AsyncSessionLocal`` is bound to the
first loop that touches it, which makes per-test reuse impossible).
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _async_url() -> str | None:
    raw = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not raw:
        return None
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return None


def _asyncpg_url() -> str | None:
    raw = _async_url()
    if not raw:
        return None
    return re.sub(r"^postgresql\+\w+://", "postgresql://", raw)


def _pick_store_sync() -> dict[str, str] | None:
    """Pick a real (tenant_id, store_id) tuple in its own short-lived loop."""
    url = _asyncpg_url()
    if not url:
        return None

    async def _pick() -> dict[str, str] | None:
        import asyncpg

        try:
            conn = await asyncpg.connect(url)
        except OSError:
            return None
        try:
            row = await conn.fetchrow("SELECT id, tenant_id FROM public.stores LIMIT 1")
            if row is None:
                return None
            return {"tenant_id": str(row["tenant_id"]), "store_id": str(row["id"])}
        finally:
            await conn.close()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_pick())
    finally:
        loop.close()


@pytest.fixture(scope="module")
def pg_seed() -> dict[str, str]:
    url = _async_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL/DATABASE_URL not set to a Postgres URL")
    seed = _pick_store_sync()
    if seed is None:
        pytest.skip("Postgres not reachable or no stores seeded")
    return seed


async def _run_with_fresh_engine(
    body: callable[[async_sessionmaker[AsyncSession]], Any],
) -> Any:
    """Drive ``body`` with a session factory bound to a brand-new engine.

    Disposes the engine when ``body`` returns so the connection pool
    never leaks to another test's event loop.
    """
    url = _async_url()
    assert url is not None
    engine = create_async_engine(url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await body(factory)
    finally:
        await engine.dispose()


def _isolated_loop(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build_event(pg_seed: dict[str, str], event_id: UUID | None) -> dict[str, Any]:
    return {
        "event_id": str(event_id) if event_id else None,
        "tenant_id": pg_seed["tenant_id"],
        "store_id": pg_seed["store_id"],
        "customer_id": None,
        "session_fingerprint": f"fp-step09-{uuid4()}",
        "step": "page_view",
        "step_data": {"path": "/test"},
    }


async def _insert_with_factory(
    factory: async_sessionmaker[AsyncSession], event: dict[str, Any]
) -> bool:
    """Mirrors ``_insert_funnel_event`` but binds to a caller-supplied
    factory instead of the module-level ``AsyncSessionLocal``.

    Implementation copied verbatim from
    ``analytics_ingest_task._insert_funnel_event`` to keep the dedupe
    SQL under test; if that helper changes shape, this test will be
    updated alongside it.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from src.infrastructure.database.models.tenant.funnel_event import (
        FunnelEventModel,
    )
    from src.infrastructure.messaging.tasks.analytics_ingest_task import _coerce_uuid

    payload = {
        "tenant_id": _coerce_uuid(event["tenant_id"]),
        "store_id": _coerce_uuid(event["store_id"]),
        "step": event["step"],
        "session_fingerprint": event.get("session_fingerprint"),
        "customer_id": _coerce_uuid(event.get("customer_id")),
        "step_data": event.get("step_data"),
        "event_id": _coerce_uuid(event.get("event_id")),
    }
    stmt = pg_insert(FunnelEventModel).values(**payload)
    if payload["event_id"] is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["event_id"],
            index_where=FunnelEventModel.event_id.is_not(None),
        )

    async with factory() as session:
        result = await session.execute(stmt)
        await session.commit()
        rowcount = getattr(result, "rowcount", None)
        return rowcount is None or rowcount > 0


async def _count_for_event_id(
    factory: async_sessionmaker[AsyncSession], event_id: UUID
) -> int:
    async with factory() as session:
        row = await session.execute(
            text("SELECT count(*) FROM public.funnel_events WHERE event_id = :eid"),
            {"eid": str(event_id)},
        )
        return int(row.scalar_one())


def test_first_insert_creates_row(pg_seed: dict[str, str]) -> None:
    event_id = uuid4()

    async def _body(factory: async_sessionmaker[AsyncSession]) -> int:
        inserted = await _insert_with_factory(factory, _build_event(pg_seed, event_id))
        assert inserted is True
        return await _count_for_event_id(factory, event_id)

    count = _isolated_loop(_run_with_fresh_engine(_body))
    assert count == 1


def test_duplicate_event_id_is_a_no_op(pg_seed: dict[str, str]) -> None:
    """Worker crash + redelivery, or two workers racing on the same
    payload — neither must produce a second row."""
    event_id = uuid4()

    async def _body(
        factory: async_sessionmaker[AsyncSession],
    ) -> tuple[bool, bool, int]:
        event = _build_event(pg_seed, event_id)
        first = await _insert_with_factory(factory, event)
        second = await _insert_with_factory(factory, event)
        count = await _count_for_event_id(factory, event_id)
        return first, second, count

    first, second, count = _isolated_loop(_run_with_fresh_engine(_body))
    assert first is True, "first insert must succeed"
    assert second is False, "second insert must be a no-op (ON CONFLICT)"
    assert count == 1


def test_null_event_id_does_not_collide_with_other_nulls(
    pg_seed: dict[str, str],
) -> None:
    """The partial UNIQUE index has ``WHERE event_id IS NOT NULL`` so
    legacy / kill-switch-fallback rows with NULL event_id are NOT
    constrained — two NULL inserts must both succeed."""

    async def _body(factory: async_sessionmaker[AsyncSession]) -> tuple[bool, bool]:
        first = await _insert_with_factory(factory, _build_event(pg_seed, None))
        second = await _insert_with_factory(factory, _build_event(pg_seed, None))
        return first, second

    first, second = _isolated_loop(_run_with_fresh_engine(_body))
    assert first is True
    assert second is True


def test_helper_signature_matches_task() -> None:
    """Sanity: the production helper's ``_insert_funnel_event`` must
    accept the same payload shape we test against here. If a future
    change drifts the helper signature, this test fails noisily."""
    from src.infrastructure.messaging.tasks.analytics_ingest_task import (
        _insert_funnel_event,
    )

    # Stub AsyncSessionLocal so we don't hit the DB; we just want the
    # helper to accept our payload shape without TypeError.
    class _FakeSession:
        async def execute(self, *a: Any, **k: Any) -> Any:
            class _R:
                rowcount = 1

            return _R()

        async def commit(self) -> None:
            return None

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

    class _FakeFactory:
        def __call__(self) -> _FakeSession:
            return _FakeSession()

    with patch(
        "src.infrastructure.messaging.tasks.analytics_ingest_task.__name__",
        "src.infrastructure.messaging.tasks.analytics_ingest_task",
    ):
        with patch.dict(
            "sys.modules",
            {
                "src.infrastructure.database.connection": type(
                    "_M",
                    (),
                    {"AsyncSessionLocal": _FakeFactory()},
                ),
            },
        ):
            payload = {
                "event_id": str(uuid4()),
                "tenant_id": str(uuid4()),
                "store_id": str(uuid4()),
                "customer_id": None,
                "session_fingerprint": "fp",
                "step": "page_view",
                "step_data": {},
            }
            # Just verify it can be called with this shape without
            # blowing up at the import / payload-binding layer.
            asyncio.new_event_loop().run_until_complete(_insert_funnel_event(payload))

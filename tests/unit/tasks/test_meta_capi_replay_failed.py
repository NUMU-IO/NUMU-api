"""A merchant replay re-queues only failed rows Meta would still accept."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.infrastructure.messaging.tasks import meta_capi


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _query):
        return _Rows(self._rows)


def test_replay_skips_rows_past_the_dedup_window(monkeypatch):
    fresh = SimpleNamespace(id=uuid.uuid4(), event_time=datetime.now(UTC))
    stale = SimpleNamespace(
        id=uuid.uuid4(), event_time=datetime.now(UTC) - timedelta(days=3)
    )
    monkeypatch.setattr(
        "src.infrastructure.database.connection.AsyncSessionLocal",
        lambda: _Session([fresh, stale]),
    )

    async def _no_rls(_session):
        return None

    monkeypatch.setattr("src.infrastructure.tenancy.rls.enable_rls_bypass", _no_rls)
    requeued = []

    async def _reschedule(_session, ids, **kwargs):
        requeued.extend(ids)
        assert kwargs["due_now"] is True

    monkeypatch.setattr(meta_capi, "_reschedule_many", _reschedule)

    result = asyncio.run(meta_capi._replay_failed(uuid.uuid4(), 500))

    assert requeued == [fresh.id]
    assert result == {"requeued": 1}

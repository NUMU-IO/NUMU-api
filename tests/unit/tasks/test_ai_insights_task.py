"""The daily AI insights task runs, and writes only its two settings keys.

It imported ``async_session_factory``, which ``database/connection.py`` does not
define, so it failed on every run (worker log 2026-10-15..18).
"""

from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.application.services import ai_insights_service
from src.infrastructure.database import connection
from src.infrastructure.messaging.tasks.ai_insights_tasks import (
    generate_ai_insights_task,
)
from src.infrastructure.repositories import (
    analytics_rollup_repository,
    store_repository,
)


class _Session:
    def __init__(self):
        self.statements, self.commits, self.rollbacks = [], 0, 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement):
        self.statements.append(statement)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def test_insights_merge_two_keys_per_store_and_survive_a_failed_store(monkeypatch):
    broken, ready, new = (
        SimpleNamespace(id=uuid4(), default_currency=None) for _ in range(3)
    )
    rollups = {ready.id: [object()] * 3, new.id: [object()]}

    class _Stores:
        def __init__(self, session):
            pass

        async def get_all(self, limit):
            return [broken, ready, new]

    class _Rollups:
        def __init__(self, session):
            pass

        async def get_range(self, store_id, date_from, date_to):
            return rollups[store_id]  # KeyError for the broken store

    async def _insights(rows, store_currency, lang):
        return {"lang": lang}

    session = _Session()
    monkeypatch.setattr(connection, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(store_repository, "StoreRepository", _Stores)
    monkeypatch.setattr(
        analytics_rollup_repository, "AnalyticsRollupRepository", _Rollups
    )
    monkeypatch.setattr(ai_insights_service, "generate_insights", _insights)

    generate_ai_insights_task.run()

    assert (session.rollbacks, session.commits) == (1, 1)
    [update] = session.statements
    sql = update.compile(dialect=postgresql.dialect())
    assert str(sql).startswith("UPDATE stores SET settings=(coalesce(stores.settings")
    assert "||" in str(sql)
    assert sql.params["id_1"] == ready.id
    assert {"ai_insights": {"lang": "ar"}, "ai_insights_en": {"lang": "en"}} in (
        sql.params.values()
    )

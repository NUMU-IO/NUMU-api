"""The tenant index has to actually persist what it reports.

It reported 15 catalog docs and wrote none: `reindex_policies` queried a table
that does not exist, and in Postgres a failed statement aborts the whole
transaction, so the catalog work written moments earlier was discarded at
commit while the caller was told it had succeeded.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.knowledge import tenant_indexer as mod


class _Embedder:
    """Counts calls — one request per product is what rate-limited the provider."""

    def __init__(self):
        self.calls = 0
        self.sizes: list[int] = []

    async def embed_passages(self, texts):
        self.calls += 1
        self.sizes.append(len(texts))
        return [[0.1] * 1024 for _ in texts]


class _Repo:
    def __init__(self):
        self.written: list[str] = []

    async def upsert_tenant_doc(self, **kw):
        self.written.append(kw["source"])
        return uuid4(), True


class _Session:
    """Enough of a session for the indexer: rows in, savepoints observed."""

    def __init__(self, rows_by_sql):
        self._rows = rows_by_sql
        self.savepoints = 0

    def begin_nested(self):
        session = self

        class _Ctx:
            async def __aenter__(self_inner):
                session.savepoints += 1
                return session

            async def __aexit__(self_inner, *a):
                return False

        return _Ctx()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        for needle, rows in self._rows.items():
            if needle in sql:

                class _R:
                    def __init__(self_inner, r):
                        self_inner._r = r

                    def all(self_inner):
                        return self_inner._r

                return _R(rows)
        raise AssertionError(f"unexpected query: {sql[:80]}")


@pytest.mark.asyncio
async def test_catalog_embeddings_are_batched(monkeypatch):
    emb, repo = _Embedder(), _Repo()
    monkeypatch.setattr(mod, "get_embedder", lambda: emb)
    monkeypatch.setattr(mod, "KnowledgeRepository", lambda s: repo)

    products = [(uuid4(), f"product {i}", "desc") for i in range(45)]
    session = _Session({"FROM public.products": products})

    n = await mod.reindex_catalog(session, tenant_id=uuid4(), store_id=uuid4())

    assert n == 45
    assert len(repo.written) == 45
    # 45 products at a batch of 20 is three requests, not forty-five.
    assert emb.calls == 3
    assert emb.sizes == [20, 20, 5]


@pytest.mark.asyncio
async def test_policies_are_read_from_the_store_settings_json(monkeypatch):
    emb, repo = _Embedder(), _Repo()
    monkeypatch.setattr(mod, "get_embedder", lambda: emb)
    monkeypatch.setattr(mod, "KnowledgeRepository", lambda s: repo)

    session = _Session({
        "settings -> 'policies'": [({"returns": "14 days", "shipping": "2-5 days"},)]
    })
    n = await mod.reindex_policies(session, tenant_id=uuid4(), store_id=uuid4())

    assert n == 2
    assert sorted(repo.written) == ["tenant-policy/returns", "tenant-policy/shipping"]


@pytest.mark.asyncio
async def test_a_store_with_no_policies_indexes_nothing_and_does_not_fail(monkeypatch):
    monkeypatch.setattr(mod, "get_embedder", lambda: _Embedder())
    monkeypatch.setattr(mod, "KnowledgeRepository", lambda s: _Repo())
    session = _Session({"settings -> 'policies'": [(None,)]})
    assert await mod.reindex_policies(session, tenant_id=uuid4(), store_id=uuid4()) == 0


@pytest.mark.asyncio
async def test_each_source_query_runs_in_its_own_savepoint(monkeypatch):
    """Without this a missing optional source discards the whole index."""
    monkeypatch.setattr(mod, "get_embedder", lambda: _Embedder())
    monkeypatch.setattr(mod, "KnowledgeRepository", lambda s: _Repo())
    session = _Session({"FROM public.products": []})
    await mod.reindex_catalog(session, tenant_id=uuid4(), store_id=uuid4())
    assert session.savepoints == 1

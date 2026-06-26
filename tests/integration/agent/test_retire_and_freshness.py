"""US4 — no-deploy freshness + retire (SC-004, FR-007).

A published article is retrievable; retiring it (status flip, reversible) removes it
from retrieval; re-publishing surfaces it again. No code deploy involved — just a
status change through the repository (what the secret-guarded /retire endpoint does).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository

_SOURCE = "numu-corpus/payments/paymob"
_BODY = "Set up Paymob under Settings then Payments and enter your API key and integration id."


async def _publish(session):
    embedder = get_embedder()
    embeddings = await embedder.embed_passages([_BODY])
    await KnowledgeRepository(session).upsert_shared_doc(
        source=_SOURCE,
        title="Set up Paymob",
        section="Payments",
        locale="en",
        chunks=[_BODY],
        embeddings=embeddings,
        area="payments",
        status="published",
    )


async def _find(session) -> bool:
    embedder = get_embedder()
    q = await embedder.embed_query("how do I set up paymob")
    results = await KnowledgeRepository(session).search(q, tenant_id=uuid4(), k=5)
    return any(r["doc"]["source"] == _SOURCE for r in results)


@pytest.mark.asyncio
async def test_retire_removes_then_republish_restores(test_session):
    await _publish(test_session)
    assert await _find(test_session) is True

    repo = KnowledgeRepository(test_session)
    n = await repo.set_shared_status(source=_SOURCE, status="retired")
    assert n >= 1
    assert await _find(test_session) is False  # FR-007: retired content not surfaced

    await repo.set_shared_status(source=_SOURCE, status="published")
    assert await _find(test_session) is True  # reversible

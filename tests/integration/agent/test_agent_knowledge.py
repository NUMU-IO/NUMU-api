"""US4 NUMU-grounded help (spec US4 scenarios 1 & 2, FR-017/FR-018).

Uses the deterministic fallback embedder (no AGENT_EMBED_URL in tests). Verifies
retrieval returns a cited chunk for a documented topic, and returns nothing for
an empty corpus so the agent says it isn't documented rather than guessing.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.tools import ToolContext
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository
from src.infrastructure.agent.tools.knowledge import search_knowledge

_PAYMOB = (
    "To accept card payments with Paymob on NUMU, go to Settings then Payments, choose "
    "Paymob, and enter your Paymob API key and integration id."
)


async def _seed_paymob(session) -> None:
    embedder = get_embedder()
    embeddings = await embedder.embed_passages([_PAYMOB])
    await KnowledgeRepository(session).upsert_shared_doc(
        source="numu-docs/payments/paymob",
        title="Set up Paymob payments",
        section="Payments",
        locale="en",
        chunks=[_PAYMOB],
        embeddings=embeddings,
    )


@pytest.mark.asyncio
async def test_search_ranks_and_cites_matching_doc(test_session):
    await _seed_paymob(test_session)
    embedder = get_embedder()
    q = await embedder.embed_query("how do I set up paymob payments")
    results = await KnowledgeRepository(test_session).search(q, tenant_id=uuid4(), k=3)

    assert results
    assert results[0]["doc"]["title"] == "Set up Paymob payments"
    assert results[0]["doc"]["layer"] == "shared"
    assert results[0]["score"] > 0


@pytest.mark.asyncio
async def test_tool_returns_chunks_with_citation(test_session):
    await _seed_paymob(test_session)
    ctx = ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=test_session,
        locale="en",
    )
    result = await search_knowledge(ctx, {"query": "set up paymob"})
    assert result.ok
    assert result.data["chunks"]
    assert result.source and result.source[0]["title"] == "Set up Paymob payments"


@pytest.mark.asyncio
async def test_tool_empty_corpus_says_undocumented(test_session):
    ctx = ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=test_session,
        locale="en",
    )
    result = await search_knowledge(
        ctx, {"query": "how do I configure quantum shipping"}
    )
    assert result.ok
    assert result.data["chunks"] == []  # nothing → agent says it isn't documented

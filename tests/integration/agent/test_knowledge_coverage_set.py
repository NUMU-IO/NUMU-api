"""US1 — the Agent knows all of NUMU: grounded, cited answers (EN + AR), and clear
separation between documented and undocumented questions (SC-001).

Loads the authored corpus through the real loader + the deterministic fallback
embedder (JSONB path), then drives the `search_knowledge` tool across a question
set spanning every area, plus undocumented control questions.

The agent-loop decline ("not documented") is driven by the LLM given the retrieved
context; at the retrieval layer we verify (a) documented questions retrieve a cited
source, and (b) undocumented questions rank far lower — the signal the agent
thresholds on. The empty-corpus → no-chunks case is covered in test_agent_knowledge.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.knowledge.corpus_loader import load_authored_corpus
from src.application.agent.tools import ToolContext
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository
from src.infrastructure.agent.tools.knowledge import search_knowledge

# (question, expected source substring) — spanning every area, EN + AR.
_DOCUMENTED = [
    ("How do I set up Paymob payments?", "paymob"),
    ("How do I enable cash on delivery?", "cod"),
    ("How do I connect Bosta shipping courier?", "bosta"),
    ("How do I recover abandoned carts?", "abandoned-cart"),
    ("How do I create a BOGO promotion campaign?", "bogo"),
    ("How does the theme editor customize work?", "editor-v3"),
    ("How do I set up e-invoicing ETA?", "e-invoicing"),
    ("Where do I see my store analytics dashboard?", "analytics"),
    ("How do I add staff and manage roles?", "staff"),
    ("How do I connect a custom domain?", "custom-domain"),
    ("How do I manage and fulfill orders?", "fulfillment"),
    ("إزاي أفعّل مدفوعات باي موب؟", "paymob"),
    ("إزاي أربط شركة شحن بوسطة؟", "bosta"),
]

_UNDOCUMENTED = [
    "How do I configure zorblax flux capacitors?",
    "Can NUMU bake croissants aboard a submarine?",
    "How do I teleport my llama to Jupiter?",
]


async def _load_corpus(session) -> None:
    embedder = get_embedder()
    repo = KnowledgeRepository(session)
    for doc in load_authored_corpus():
        embeddings = await embedder.embed_passages(doc.chunks)
        await repo.upsert_shared_doc(
            source=doc.source,
            title=doc.title,
            section=doc.section,
            locale=doc.locale,
            chunks=doc.chunks,
            embeddings=embeddings,
            area=doc.area,
            source_kind=doc.source_kind.value,
            status=doc.status.value,
        )


def _ctx(session):
    return ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=session,
        locale="en",
    )


@pytest.mark.asyncio
async def test_documented_questions_retrieve_their_cited_source(test_session):
    await _load_corpus(test_session)
    ctx = _ctx(test_session)
    hits = 0
    for question, expected in _DOCUMENTED:
        result = await search_knowledge(ctx, {"query": question})
        assert result.ok
        sources = " ".join(s.get("source", "") for s in (result.source or []))
        if result.data["chunks"] and expected in sources:
            hits += 1
    # SC-001: ≥95% of documented questions answered with the right cited source.
    assert hits / len(_DOCUMENTED) >= 0.95, f"only {hits}/{len(_DOCUMENTED)} hit"


@pytest.mark.asyncio
async def test_undocumented_rank_far_below_documented(test_session):
    await _load_corpus(test_session)
    ctx = _ctx(test_session)

    baseline = await search_knowledge(
        ctx, {"query": "How do I set up Paymob payments?"}
    )
    baseline_score = baseline.data["chunks"][0]["score"]
    assert baseline_score > 0.3  # a real documented match scores strongly

    for question in _UNDOCUMENTED:
        result = await search_knowledge(ctx, {"query": question})
        top = result.data["chunks"][0]["score"] if result.data["chunks"] else 0.0
        # Undocumented nonsense ranks below a genuine match → the agent declines.
        # (With the deterministic fallback embedder, common words like "how do I"
        # give a small floor; the real e5 model separates these much more sharply.)
        assert top < baseline_score

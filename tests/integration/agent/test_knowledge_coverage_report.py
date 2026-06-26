"""US1/US4 — coverage & provenance (SC-002, SC-008).

After loading the authored corpus, every NUMU area has at least one published
article (no blind spots) and every doc carries provenance + last-updated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.application.agent.knowledge.corpus_loader import (
    load_areas,
    load_authored_corpus,
)
from src.application.agent.knowledge.coverage import build_coverage_report
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.models import NumuKnowledgeDocModel
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository


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


@pytest.mark.asyncio
async def test_every_area_has_a_published_article(test_session):
    await _load_corpus(test_session)
    report = await build_coverage_report(test_session)

    gaps = [a.area for a in report.areas if a.is_gap]
    # 'growth' is covered by playbooks (loaded with the authored corpus); every
    # other area is covered by how-to. SC-002 = 100% of areas covered.
    assert gaps == [], f"areas with no published article: {gaps}"
    assert report.summary["coverage_pct"] == 100.0
    assert {a.area for a in report.areas} == {a.key for a in load_areas()}


@pytest.mark.asyncio
async def test_all_docs_carry_provenance_and_timestamp(test_session):
    await _load_corpus(test_session)
    rows = await test_session.execute(select(NumuKnowledgeDocModel))
    docs = rows.scalars().all()
    assert docs
    for d in docs:
        assert d.source and d.title and d.area  # provenance (SC-008)
        assert d.updated_at is not None  # last-updated (SC-008)

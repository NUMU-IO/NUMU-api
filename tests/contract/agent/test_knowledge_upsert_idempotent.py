"""Idempotent knowledge upsert (FR-010, spec 002).

Re-running an upsert with unchanged content must NOT duplicate chunks and must
report the doc as unchanged. Changing the content replaces the chunks in place
(still keyed on `source`). Uses the deterministic fallback embedder + JSONB path.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.models import (
    NumuKnowledgeChunkModel,
    NumuKnowledgeDocModel,
)
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository

_SOURCE = "numu-corpus/payments/paymob"
_CHUNKS = ["Set up Paymob under Settings then Payments and enter your API key."]


async def _upsert(session, chunks):
    embedder = get_embedder()
    embeddings = await embedder.embed_passages(chunks)
    return await KnowledgeRepository(session).upsert_shared_doc(
        source=_SOURCE,
        title="Set up Paymob",
        section="Payments",
        locale="en",
        chunks=chunks,
        embeddings=embeddings,
        area="payments",
        status="published",
    )


async def _counts(session):
    docs = await session.execute(
        select(func.count())
        .select_from(NumuKnowledgeDocModel)
        .where(NumuKnowledgeDocModel.source == _SOURCE)
    )
    chunks = await session.execute(
        select(func.count())
        .select_from(NumuKnowledgeChunkModel)
        .join(
            NumuKnowledgeDocModel,
            NumuKnowledgeChunkModel.doc_id == NumuKnowledgeDocModel.id,
        )
        .where(NumuKnowledgeDocModel.source == _SOURCE)
    )
    return docs.scalar_one(), chunks.scalar_one()


@pytest.mark.asyncio
async def test_reupsert_same_content_is_noop(test_session):
    _id1, changed1 = await _upsert(test_session, _CHUNKS)
    assert changed1 is True
    docs1, chunks1 = await _counts(test_session)

    _id2, changed2 = await _upsert(test_session, _CHUNKS)  # identical content
    assert changed2 is False  # skipped_unchanged
    docs2, chunks2 = await _counts(test_session)

    assert docs2 == docs1 == 1
    assert chunks2 == chunks1  # no duplication


@pytest.mark.asyncio
async def test_reupsert_changed_content_replaces(test_session):
    await _upsert(test_session, _CHUNKS)
    _id, changed = await _upsert(
        test_session, ["A completely different Paymob instruction body."]
    )
    assert changed is True
    docs, _chunks = await _counts(test_session)
    assert docs == 1  # still one doc for this source (no orphan/duplicate)

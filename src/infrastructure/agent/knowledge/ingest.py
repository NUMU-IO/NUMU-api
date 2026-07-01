"""Ingest the authored Layer-A corpus into the shared knowledge store.

Runnable at deploy time so the NUMU knowledge base is never empty. Loads the
in-repo how-to + playbook articles (`corpus/howto`, `corpus/playbooks`), embeds
each chunk, and upserts them into Layer A. The upsert is idempotent — keyed on
`source` + content hash — so re-running on every deploy skips unchanged docs and
only re-embeds what actually changed (FR-010). Shared docs carry no tenant, so
this needs no tenant/RLS context.

    python -m src.infrastructure.agent.knowledge.ingest

Note: retrieval quality still depends on a real embedding model. With
`AGENT_EMBED_URL` unset the deterministic hash fallback is used, which stores
valid vectors but only keyword-ish similarity — configure a real embeddings
endpoint (Step 2) and re-run to lift retrieval quality.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from src.application.agent.knowledge.corpus_loader import load_authored_corpus
from src.config.logging_config import get_logger
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository
from src.infrastructure.database.connection import AsyncSessionLocal

logger = get_logger(__name__)


async def ingest_authored_corpus() -> dict:
    """Embed + idempotently upsert every authored Layer-A doc. Returns a summary."""
    docs = load_authored_corpus()
    embedder = get_embedder()
    upserted = skipped = chunk_count = 0

    async with AsyncSessionLocal() as session:
        # Shared knowledge lives in the public schema; a bare session (no tenant
        # middleware) may inherit a different search_path, so pin it explicitly.
        await session.execute(text("SET search_path TO public"))
        repo = KnowledgeRepository(session)
        for doc in docs:
            embeddings = await embedder.embed_passages(doc.chunks)
            _doc_id, changed = await repo.upsert_shared_doc(
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
            if changed:
                upserted += 1
                chunk_count += len(doc.chunks)
            else:
                skipped += 1
        await session.commit()

    result = {
        "docs": len(docs),
        "upserted": upserted,
        "skipped_unchanged": skipped,
        "chunks": chunk_count,
    }
    logger.info("agent_knowledge_ingest_authored", **result)
    return result


def main() -> None:
    result = asyncio.run(ingest_authored_corpus())
    print(f"[knowledge-ingest] {result}")


if __name__ == "__main__":
    main()

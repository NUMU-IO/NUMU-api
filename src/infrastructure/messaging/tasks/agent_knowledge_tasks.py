"""Keep the agent's knowledge base current (Phase I).

`POST /agent/knowledge/refresh` has existed since the knowledge base shipped,
and nothing has ever called it on a schedule — no beat entry, no cron. The
corpus was therefore frozen at whatever someone last ingested by hand, which
until 2026-09-08 was nothing at all. An agent answering "how do I connect
Bosta" from a stale corpus is worse than one that says it does not know,
because it sounds equally confident either way.

Celery rather than n8n, deliberately. This is scheduled, bulk, retry-tolerant
work with one source — the in-repo corpus — and Celery beat already runs in
this stack. n8n earns the job when the sources become external SaaS or when
someone who does not write Python needs to edit the pipeline; the
`trigger_workflow` lane is already there for that day.

Idempotent by content hash: a document whose text and embedder signature have
not changed is skipped, so a nightly run over an unchanged corpus writes
nothing.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop = None


def _run(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(name="tasks.refresh_agent_knowledge", bind=True, max_retries=1)
def refresh_agent_knowledge_task(self):
    """Re-ingest the authored corpus. Nightly."""
    try:
        return _run(_refresh())
    except Exception as exc:
        logger.exception("agent_knowledge_refresh_failed")
        # One retry, ten minutes out: the usual cause is the embedding
        # provider being briefly unavailable, which fixes itself.
        raise self.retry(exc=exc, countdown=600)


async def _refresh() -> dict:
    from src.api.v1.agent.knowledge_routes import _embed_and_upsert
    from src.application.agent.knowledge.corpus_loader import load_authored_corpus
    from src.infrastructure.database.connection import AsyncSessionLocal

    docs = load_authored_corpus()
    async with AsyncSessionLocal() as session:
        result = await _embed_and_upsert(session, docs)
        await session.commit()

    logger.info(
        "agent_knowledge_refreshed",
        extra={"source_kind": "authored", **result},
    )
    return result

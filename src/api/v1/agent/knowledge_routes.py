"""Knowledge ingestion, refresh, retire & coverage endpoints (Layer A).

These manage the SHARED NUMU corpus (no merchant data). They are not store-scoped
and are authenticated by a server-side secret header (`X-Knowledge-Secret`), never a
user session (research R9/R11) — called by n8n / CI / platform tooling. Per-tenant
Layer B is managed through the store-scoped notes API + the tenant indexer.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.application.agent.knowledge.corpus_loader import load_authored_corpus
from src.application.agent.knowledge.coverage import build_coverage_report
from src.config import settings as app_settings
from src.config.logging_config import get_logger
from src.infrastructure.agent.knowledge.docs_ingest import fetch_docs_corpus
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository
from src.infrastructure.agent.knowledge.seed_corpus import seed_shared_corpus

logger = get_logger(__name__)

router = APIRouter(prefix="/agent/knowledge", tags=["Agent"])


class KnowledgeDocIn(BaseModel):
    source: str
    title: str
    section: str | None = None
    locale: str = "en"
    area: str | None = None
    source_kind: str = "authored"
    status: str = "published"
    chunks: list[str] = Field(..., min_length=1)


class UpsertRequest(BaseModel):
    docs: list[KnowledgeDocIn] = Field(..., min_length=1)


class RefreshRequest(BaseModel):
    source_kind: str = Field(..., description="authored | docs")
    areas: list[str] | None = None


class RetireRequest(BaseModel):
    source: str
    status: str = "retired"  # retired | published


class TenantReindexRequest(BaseModel):
    tenant_id: UUID
    store_id: UUID
    scope: str = "all"  # catalog | policy | all


def _check_secret(provided: str | None) -> None:
    secret = app_settings.agent_knowledge_upsert_secret
    if not secret or provided != secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": "Invalid or missing knowledge secret.",
            },
        )


async def _embed_and_upsert(db: AsyncSession, docs) -> dict:
    """Embed + idempotently upsert a list of KnowledgeDoc/KnowledgeDocIn-like items."""
    embedder = get_embedder()
    repo = KnowledgeRepository(db)
    upserted_docs = upserted_chunks = skipped = 0
    for doc in docs:
        chunks = doc.chunks
        embeddings = await embedder.embed_passages(chunks)
        _doc_id, changed = await repo.upsert_shared_doc(
            source=doc.source,
            title=doc.title,
            section=getattr(doc, "section", None),
            locale=getattr(doc, "locale", "en"),
            chunks=chunks,
            embeddings=embeddings,
            area=getattr(doc, "area", None),
            source_kind=_kind_str(getattr(doc, "source_kind", "authored")),
            status=_status_str(getattr(doc, "status", "published")),
        )
        if changed:
            upserted_docs += 1
            upserted_chunks += len(chunks)
        else:
            skipped += 1
    return {
        "upserted_docs": upserted_docs,
        "upserted_chunks": upserted_chunks,
        "skipped_unchanged": skipped,
    }


def _kind_str(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _status_str(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


@router.post("/upsert")
async def upsert_knowledge(
    body: UpsertRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_knowledge_secret: Annotated[str | None, Header()] = None,
) -> dict:
    _check_secret(x_knowledge_secret)
    result = await _embed_and_upsert(db, body.docs)
    logger.info("agent_knowledge_upserted", **result)
    return result


@router.post("/refresh")
async def refresh_knowledge(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_knowledge_secret: Annotated[str | None, Header()] = None,
) -> dict:
    """(Re)build a source set — authored in-repo corpus or ingested developer docs."""
    _check_secret(x_knowledge_secret)
    if body.source_kind == "authored":
        docs = load_authored_corpus()
    elif body.source_kind == "docs":
        docs = await fetch_docs_corpus()
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_source_kind",
                "message": "source_kind must be 'authored' or 'docs'.",
            },
        )
    if body.areas:
        docs = [d for d in docs if d.area in set(body.areas)]
    result = await _embed_and_upsert(db, docs)
    result["status"] = "completed"
    logger.info("agent_knowledge_refreshed", source_kind=body.source_kind, **result)
    return result


@router.post("/retire")
async def retire_knowledge(
    body: RetireRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_knowledge_secret: Annotated[str | None, Header()] = None,
) -> dict:
    """Retire (or un-retire) shared docs by source — reversible, never deleted."""
    _check_secret(x_knowledge_secret)
    if body.status not in ("retired", "published"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_status",
                "message": "status must be retired|published",
            },
        )
    affected = await KnowledgeRepository(db).set_shared_status(
        source=body.source, status=body.status
    )
    logger.info(
        "agent_knowledge_retire",
        source=body.source,
        status=body.status,
        affected=affected,
    )
    return {"source": body.source, "status": body.status, "affected_docs": affected}


@router.get("/coverage")
async def coverage_report(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_knowledge_secret: Annotated[str | None, Header()] = None,
) -> dict:
    """Coverage / freshness report — areas documented & current vs. gaps/stale (FR-011)."""
    _check_secret(x_knowledge_secret)
    report = await build_coverage_report(db)
    return {
        "generated_at": report.generated_at.isoformat()
        if report.generated_at
        else None,
        "staleness_days": report.staleness_days,
        "areas": [
            {
                "area": a.area,
                "published_count": a.published_count,
                "newest_updated_at": a.newest_updated_at.isoformat()
                if a.newest_updated_at
                else None,
                "is_gap": a.is_gap,
                "is_stale": a.is_stale,
            }
            for a in report.areas
        ],
        "summary": report.summary,
    }


@router.post("/tenant-reindex")
async def tenant_reindex(
    body: TenantReindexRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_knowledge_secret: Annotated[str | None, Header()] = None,
) -> dict:
    """Re-embed a tenant's Layer B (catalog/policies) — called by the n8n reindex lane.

    Secret-guarded (not a user session). Tenant isolation is enforced server-side: we
    set the RLS GUC from the trusted payload tenant_id before any Layer-B write, so the
    reindex can never touch another tenant (SC-005)."""
    _check_secret(x_knowledge_secret)
    from src.application.agent.knowledge.tenant_indexer import reindex_tenant
    from src.infrastructure.tenancy.rls import set_tenant_context

    await set_tenant_context(db, body.tenant_id)
    result = await reindex_tenant(db, tenant_id=body.tenant_id, store_id=body.store_id)
    logger.info("agent_tenant_reindexed", tenant_id=str(body.tenant_id), **result)
    return {"status": "completed", **result}


@router.post("/seed")
async def seed_knowledge(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_knowledge_secret: Annotated[str | None, Header()] = None,
) -> dict:
    """Seed the bundled starter corpus (dev/bootstrap convenience)."""
    _check_secret(x_knowledge_secret)
    count = await seed_shared_corpus(db)
    return {"seeded_chunks": count}

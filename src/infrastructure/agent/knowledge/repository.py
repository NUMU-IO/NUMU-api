"""Two-layer knowledge retrieval + upsert — spec 002 dual-path (pgvector | JSONB).

Retrieval merges Layer A (shared, published) with ONLY the caller tenant's Layer B
(published) and returns top-k chunks with their source doc for citation.

**Dual-path, soft-added pgvector**: when the `vector` extension is active and the
`embedding_vec` column is populated, ranking runs in SQL (`embedding_vec <=> :q`,
cosine). Otherwise it falls back to 001's Python dot-product scan over the JSONB
`embedding` column. Embeddings are L2-normalized, so cosine == dot product and both
paths agree on ordering. The `search()` signature is unchanged, so the
`search_knowledge` tool is untouched.

Upserts are idempotent (keyed on `source`): re-running with an unchanged
`content_hash` is a no-op (FR-010); they write JSONB always (fallback) and
`embedding_vec` when pgvector is active.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.agent.knowledge.models import (
    NumuKnowledgeChunkModel,
    NumuKnowledgeDocModel,
    TenantKnowledgeChunkModel,
    TenantKnowledgeDocModel,
)


def _dot(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))


def compute_content_hash(chunks: list[str]) -> str:
    """Stable hash of a doc's normalized chunk text (idempotency short-circuit)."""
    norm = "\n\n".join((c or "").strip() for c in chunks)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _vector_literal(embedding: list[float]) -> str:
    """pgvector text literal, e.g. '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._pgvector: bool | None = None

    # ── capability probe ────────────────────────────────────────────────────
    async def _has_pgvector(self) -> bool:
        if self._pgvector is None:
            # Only Postgres can have the extension; skip the probe on SQLite (tests).
            dialect = getattr(self.session.bind, "dialect", None)
            if dialect is not None and dialect.name != "postgresql":
                self._pgvector = False
                return self._pgvector
            try:
                row = await self.session.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                )
                self._pgvector = row.first() is not None
            except Exception:  # noqa: BLE001 — degrade to JSONB path
                self._pgvector = False
        return self._pgvector

    # ── upsert (Layer A) ────────────────────────────────────────────────────
    async def upsert_shared_doc(
        self,
        *,
        source: str,
        title: str,
        section: str | None,
        locale: str,
        chunks: list[str],
        embeddings: list[list[float]],
        area: str | None = None,
        source_kind: str = "authored",
        status: str = "published",
    ) -> tuple[UUID | None, bool]:
        """Idempotent upsert keyed on `source`. Returns (doc_id, changed).

        Skips re-embed/replace when the content hash is unchanged (FR-010)."""
        content_hash = compute_content_hash(chunks)

        existing = await self.session.execute(
            select(NumuKnowledgeDocModel).where(NumuKnowledgeDocModel.source == source)
        )
        existing_docs = existing.scalars().all()
        if existing_docs:
            # Unchanged → no-op (but keep status/metadata current).
            if (
                len(existing_docs) == 1
                and existing_docs[0].content_hash == content_hash
            ):
                doc = existing_docs[0]
                doc.status = status
                doc.title = title
                doc.area = area
                await self.session.flush()
                return doc.id, False
            for doc in existing_docs:
                await self.session.execute(
                    delete(NumuKnowledgeChunkModel).where(
                        NumuKnowledgeChunkModel.doc_id == doc.id
                    )
                )
                await self.session.execute(
                    delete(NumuKnowledgeDocModel).where(
                        NumuKnowledgeDocModel.id == doc.id
                    )
                )

        doc = NumuKnowledgeDocModel(
            id=uuid4(),
            source=source,
            title=title,
            section=section,
            locale=locale,
            area=area,
            source_kind=source_kind,
            status=status,
            content_hash=content_hash,
        )
        self.session.add(doc)
        await self.session.flush()
        await self._insert_chunks(
            NumuKnowledgeChunkModel, doc.id, chunks, embeddings, tenant_id=None
        )
        return doc.id, True

    # ── upsert (Layer B) ────────────────────────────────────────────────────
    async def upsert_tenant_doc(
        self,
        *,
        tenant_id: UUID,
        source: str,
        title: str,
        section: str | None,
        locale: str,
        chunks: list[str],
        embeddings: list[list[float]],
        source_kind: str = "note",
        status: str = "published",
    ) -> tuple[UUID | None, bool]:
        """Idempotent per-tenant upsert keyed on (tenant_id, source)."""
        content_hash = compute_content_hash(chunks)
        existing = await self.session.execute(
            select(TenantKnowledgeDocModel).where(
                TenantKnowledgeDocModel.tenant_id == tenant_id,
                TenantKnowledgeDocModel.source == source,
            )
        )
        existing_docs = existing.scalars().all()
        if existing_docs:
            if (
                len(existing_docs) == 1
                and existing_docs[0].content_hash == content_hash
            ):
                doc = existing_docs[0]
                doc.status = status
                doc.title = title
                await self.session.flush()
                return doc.id, False
            for doc in existing_docs:
                await self.session.execute(
                    delete(TenantKnowledgeChunkModel).where(
                        TenantKnowledgeChunkModel.doc_id == doc.id
                    )
                )
                await self.session.execute(
                    delete(TenantKnowledgeDocModel).where(
                        TenantKnowledgeDocModel.id == doc.id
                    )
                )

        doc = TenantKnowledgeDocModel(
            id=uuid4(),
            tenant_id=tenant_id,
            source=source,
            title=title,
            section=section,
            locale=locale,
            source_kind=source_kind,
            status=status,
            content_hash=content_hash,
        )
        self.session.add(doc)
        await self.session.flush()
        await self._insert_chunks(
            TenantKnowledgeChunkModel, doc.id, chunks, embeddings, tenant_id=tenant_id
        )
        return doc.id, True

    async def retire_tenant_doc_for_source(
        self, *, tenant_id: UUID, source: str
    ) -> int:
        """Drop a tenant doc's chunks + doc (used when a note is retired). Returns rows."""
        rows = await self.session.execute(
            select(TenantKnowledgeDocModel.id).where(
                TenantKnowledgeDocModel.tenant_id == tenant_id,
                TenantKnowledgeDocModel.source == source,
            )
        )
        ids = [r[0] for r in rows.all()]
        for doc_id in ids:
            await self.session.execute(
                delete(TenantKnowledgeChunkModel).where(
                    TenantKnowledgeChunkModel.doc_id == doc_id
                )
            )
            await self.session.execute(
                delete(TenantKnowledgeDocModel).where(
                    TenantKnowledgeDocModel.id == doc_id
                )
            )
        return len(ids)

    async def set_shared_status(self, *, source: str, status: str) -> int:
        """Retire/un-retire shared docs by source (reversible). Returns rows changed."""
        result = await self.session.execute(
            select(NumuKnowledgeDocModel).where(NumuKnowledgeDocModel.source == source)
        )
        docs = result.scalars().all()
        for doc in docs:
            doc.status = status
        await self.session.flush()
        return len(docs)

    async def _insert_chunks(
        self, model, doc_id, chunks, embeddings, *, tenant_id
    ) -> None:
        use_vec = await self._has_pgvector()
        for chunk_text, emb in zip(chunks, embeddings, strict=False):
            kwargs = {
                "id": uuid4(),
                "doc_id": doc_id,
                "content": chunk_text,
                "embedding": emb,
                "token_count": len(chunk_text.split()),
            }
            if tenant_id is not None:
                kwargs["tenant_id"] = tenant_id
            if use_vec:
                kwargs["embedding_vec"] = emb
            self.session.add(model(**kwargs))
        await self.session.flush()

    # ── retrieval (dual-path) ───────────────────────────────────────────────
    async def search(
        self, query_embedding: list[float], *, tenant_id: UUID, k: int = 5
    ) -> list[dict]:
        if await self._has_pgvector():
            try:
                return await self._search_pgvector(
                    query_embedding, tenant_id=tenant_id, k=k
                )
            except Exception:  # noqa: BLE001 — fall back if the ANN path errors
                pass
        return await self._search_jsonb(query_embedding, tenant_id=tenant_id, k=k)

    async def _search_pgvector(self, query_embedding, *, tenant_id, k) -> list[dict]:
        qv = _vector_literal(query_embedding)
        results: list[dict] = []

        rows_a = await self.session.execute(
            text(
                "SELECT c.content, (1 - (c.embedding_vec <=> CAST(:qv AS vector))) AS score, "
                "d.id, d.title, d.source "
                "FROM public.numu_knowledge_chunks c "
                "JOIN public.numu_knowledge_docs d ON c.doc_id = d.id "
                "WHERE d.status = 'published' AND c.embedding_vec IS NOT NULL "
                "ORDER BY c.embedding_vec <=> CAST(:qv AS vector) LIMIT :k"
            ),
            {"qv": qv, "k": k},
        )
        for content, score, did, title, source in rows_a.all():
            results.append({
                "content": content,
                "score": float(score),
                "doc": {
                    "id": str(did),
                    "title": title,
                    "source": source,
                    "layer": "shared",
                },
            })

        rows_b = await self.session.execute(
            text(
                "SELECT c.content, (1 - (c.embedding_vec <=> CAST(:qv AS vector))) AS score, "
                "d.id, d.title, d.source "
                "FROM public.tenant_knowledge_chunks c "
                "JOIN public.tenant_knowledge_docs d ON c.doc_id = d.id "
                "WHERE d.status = 'published' AND c.embedding_vec IS NOT NULL "
                "AND c.tenant_id = :tid "
                "ORDER BY c.embedding_vec <=> CAST(:qv AS vector) LIMIT :k"
            ),
            {"qv": qv, "k": k, "tid": str(tenant_id)},
        )
        for content, score, did, title, source in rows_b.all():
            results.append({
                "content": content,
                "score": float(score),
                "doc": {
                    "id": str(did),
                    "title": title,
                    "source": source,
                    "layer": "tenant",
                },
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:k]

    async def _search_jsonb(self, query_embedding, *, tenant_id, k) -> list[dict]:
        results: list[dict] = []

        rows = await self.session.execute(
            select(NumuKnowledgeChunkModel, NumuKnowledgeDocModel)
            .join(
                NumuKnowledgeDocModel,
                NumuKnowledgeChunkModel.doc_id == NumuKnowledgeDocModel.id,
            )
            .where(NumuKnowledgeDocModel.status == "published")
        )
        for chunk, doc in rows.all():
            results.append({
                "content": chunk.content,
                "score": _dot(query_embedding, chunk.embedding or []),
                "doc": {
                    "id": str(doc.id),
                    "title": doc.title,
                    "source": doc.source,
                    "layer": "shared",
                },
            })

        rows_b = await self.session.execute(
            select(TenantKnowledgeChunkModel, TenantKnowledgeDocModel)
            .join(
                TenantKnowledgeDocModel,
                TenantKnowledgeChunkModel.doc_id == TenantKnowledgeDocModel.id,
            )
            .where(
                TenantKnowledgeChunkModel.tenant_id == tenant_id,
                TenantKnowledgeDocModel.status == "published",
            )
        )
        for chunk, doc in rows_b.all():
            results.append({
                "content": chunk.content,
                "score": _dot(query_embedding, chunk.embedding or []),
                "doc": {
                    "id": str(doc.id),
                    "title": doc.title,
                    "source": doc.source,
                    "layer": "tenant",
                },
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:k]

    # ── coverage report (FR-011) ────────────────────────────────────────────
    async def coverage_rows(self) -> list[dict]:
        """Raw per-area coverage values (app applies the staleness threshold).

        Computed directly from the docs table (GROUP BY area) so it is DB-agnostic —
        the `numu_knowledge_coverage` view is an equivalent convenience for ad-hoc SQL,
        but the app does not depend on it (and it is absent on the SQLite test harness).
        """
        from sqlalchemy import func

        rows = await self.session.execute(
            select(
                NumuKnowledgeDocModel.area,
                func.count(NumuKnowledgeDocModel.id),
                func.max(NumuKnowledgeDocModel.updated_at),
            )
            .where(
                NumuKnowledgeDocModel.area.is_not(None),
                NumuKnowledgeDocModel.status == "published",
            )
            .group_by(NumuKnowledgeDocModel.area)
        )
        return [
            {"area": area, "published_count": int(pc or 0), "newest_updated_at": ts}
            for area, pc, ts in rows.all()
        ]

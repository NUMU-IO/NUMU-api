"""Knowledge-base models (two layers) — spec 002 soft-adds pgvector.

Layer A (Numu*) is shared platform documentation — NO tenant_id (contains no
merchant data). Layer B (Tenant*) is the merchant's own knowledge — tenant-scoped
(RLS).

pgvector is **soft-added** (additive, non-breaking): the original JSONB `embedding`
column is KEPT as a fallback + backfill source, and a parallel `embedding_vec`
`vector(1024)` column is added. Retrieval is dual-path: pgvector ANN when the
extension is active and the column is populated, else the JSONB Python scan. The
`Vector` import is guarded so the app still boots if the `pgvector` package or the
DB extension is absent (the column then simply degrades to the JSONB path).
"""

from __future__ import annotations

from uuid import UUID as PyUUID

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)

# Guarded pgvector import — soft dependency. When unavailable, embedding_vec is
# stored as JSONB so model metadata still loads and the JSONB retrieval path works.
try:  # pragma: no cover - import guard
    from pgvector.sqlalchemy import Vector as _Vector

    EMBED_DIM = 1024

    def _embedding_vec_column() -> Mapped[list | None]:
        return mapped_column(_Vector(EMBED_DIM), nullable=True)

    PGVECTOR_AVAILABLE = True
except Exception:  # pgvector package not installed — degrade to JSONB column type
    PGVECTOR_AVAILABLE = False

    def _embedding_vec_column() -> Mapped[list | None]:
        return mapped_column(JSONB, nullable=True)


# ── Layer A — shared NUMU platform knowledge (NOT tenant-scoped) ──────────────


class NumuKnowledgeDocModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "numu_knowledge_docs"
    __table_args__ = {"schema": "public"}

    source: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    # ── spec 002: lifecycle + coverage ──────────────────────────────────────
    area: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="authored"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="published", index=True
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class NumuKnowledgeChunkModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "numu_knowledge_chunks"
    __table_args__ = {"schema": "public"}

    doc_id: Mapped[PyUUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("public.numu_knowledge_docs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list] = mapped_column(
        JSONB, nullable=False
    )  # 001 fallback (kept)
    embedding_vec: Mapped[list | None] = _embedding_vec_column()  # 002 pgvector(1024)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ── Layer B — per-tenant knowledge (RLS) ─────────────────────────────────────


class TenantKnowledgeDocModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "tenant_knowledge_docs"
    __table_args__ = {"schema": "public"}

    source: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    # ── spec 002: lifecycle ─────────────────────────────────────────────────
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="note")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="published", index=True
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TenantKnowledgeChunkModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "tenant_knowledge_chunks"
    __table_args__ = {"schema": "public"}

    doc_id: Mapped[PyUUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("public.tenant_knowledge_docs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list] = mapped_column(
        JSONB, nullable=False
    )  # 001 fallback (kept)
    embedding_vec: Mapped[list | None] = _embedding_vec_column()  # 002 pgvector(1024)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ── Layer B authoring source — merchant-written notes/FAQ (FR-004a) ──────────


class TenantNoteModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A merchant-authored note/FAQ. On publish it is embedded into the tenant's
    Layer B (`layer_b_doc_id`); on retire it leaves Layer B. Tenant-scoped (RLS)."""

    __tablename__ = "agent_tenant_notes"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[PyUUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_staff_id: Mapped[PyUUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="published", index=True
    )
    layer_b_doc_id: Mapped[PyUUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )

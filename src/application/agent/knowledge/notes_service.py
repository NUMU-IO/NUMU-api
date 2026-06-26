"""US3 — merchant-authored notes/FAQ (Layer B authoring, FR-004a).

A note is the merchant's own knowledge. On publish it is embedded and upserted into
THIS tenant's Layer B (RLS-isolated); on retire it leaves Layer B so it stops
surfacing. Every mutation is audited (Constitution III/VII) and the body is treated
strictly as data, never as an instruction to the Agent (FR-013).
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.models import TenantNoteModel
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository
from src.infrastructure.agent.persistence.models import AgentAuditLogModel

logger = get_logger(__name__)


def _note_source(note_id: UUID) -> str:
    """Stable Layer-B source key for a note's derived doc."""
    return f"tenant-note/{note_id}"


async def _audit(
    session: AsyncSession, *, tenant_id, staff_id, note_id, action, before, after
) -> UUID:
    rec = AgentAuditLogModel(
        id=uuid4(),
        tenant_id=tenant_id,
        staff_id=staff_id,
        conversation_id=None,
        tool_name=f"note.{action}",
        params={"note_id": str(note_id)},
        before_state=before or {},
        after_state=after or {},
        result="applied",
    )
    session.add(rec)
    await session.flush()
    return rec.id


async def _index_note(session: AsyncSession, note: TenantNoteModel) -> UUID | None:
    """Embed the note and upsert it into the tenant's Layer B; return the doc id."""
    embedder = get_embedder()
    text = f"{note.title}\n\n{note.body}"
    embeddings = await embedder.embed_passages([text])
    doc_id, _changed = await KnowledgeRepository(session).upsert_tenant_doc(
        tenant_id=note.tenant_id,
        source=_note_source(note.id),
        title=note.title,
        section="Merchant Notes",
        locale=note.locale,
        chunks=[text],
        embeddings=embeddings,
        source_kind="note",
        status="published",
    )
    return doc_id


async def list_notes(
    session: AsyncSession, *, tenant_id: UUID, store_id: UUID
) -> list[TenantNoteModel]:
    rows = await session.execute(
        select(TenantNoteModel)
        .where(
            TenantNoteModel.tenant_id == tenant_id,
            TenantNoteModel.store_id == store_id,
        )
        .order_by(TenantNoteModel.updated_at.desc())
    )
    return list(rows.scalars().all())


async def create_note(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    store_id: UUID,
    staff_id: UUID,
    title: str,
    body: str,
    locale: str = "en",
) -> tuple[TenantNoteModel, UUID]:
    note = TenantNoteModel(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_id,
        author_staff_id=staff_id,
        title=title,
        body=body,
        locale=locale,
        status="published",
    )
    session.add(note)
    await session.flush()
    note.layer_b_doc_id = await _index_note(session, note)
    audit_id = await _audit(
        session,
        tenant_id=tenant_id,
        staff_id=staff_id,
        note_id=note.id,
        action="create",
        before={},
        after={"title": title},
    )
    logger.info("agent_note_created", tenant_id=str(tenant_id), note_id=str(note.id))
    return note, audit_id


async def update_note(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    store_id: UUID,
    staff_id: UUID,
    note_id: UUID,
    title: str | None = None,
    body: str | None = None,
    locale: str | None = None,
) -> tuple[TenantNoteModel | None, UUID | None]:
    note = await _get(session, tenant_id, store_id, note_id)
    if note is None:
        return None, None
    before = {"title": note.title}
    if title is not None:
        note.title = title
    if body is not None:
        note.body = body
    if locale is not None:
        note.locale = locale
    note.status = "published"
    await session.flush()
    note.layer_b_doc_id = await _index_note(session, note)  # re-embed (freshness)
    audit_id = await _audit(
        session,
        tenant_id=tenant_id,
        staff_id=staff_id,
        note_id=note.id,
        action="update",
        before=before,
        after={"title": note.title},
    )
    logger.info("agent_note_updated", tenant_id=str(tenant_id), note_id=str(note.id))
    return note, audit_id


async def set_note_status(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    store_id: UUID,
    staff_id: UUID,
    note_id: UUID,
    status: str,
) -> tuple[TenantNoteModel | None, UUID | None]:
    note = await _get(session, tenant_id, store_id, note_id)
    if note is None:
        return None, None
    before = {"status": note.status}
    note.status = status
    await session.flush()
    repo = KnowledgeRepository(session)
    if status == "retired":
        await repo.retire_tenant_doc_for_source(
            tenant_id=tenant_id, source=_note_source(note.id)
        )
        note.layer_b_doc_id = None
    else:  # re-publish → re-index
        note.layer_b_doc_id = await _index_note(session, note)
    audit_id = await _audit(
        session,
        tenant_id=tenant_id,
        staff_id=staff_id,
        note_id=note.id,
        action="retire" if status == "retired" else "publish",
        before=before,
        after={"status": status},
    )
    logger.info(
        "agent_note_status",
        tenant_id=str(tenant_id),
        note_id=str(note.id),
        status=status,
    )
    return note, audit_id


async def _get(
    session: AsyncSession, tenant_id: UUID, store_id: UUID, note_id: UUID
) -> TenantNoteModel | None:
    rows = await session.execute(
        select(TenantNoteModel).where(
            TenantNoteModel.id == note_id,
            TenantNoteModel.tenant_id == tenant_id,
            TenantNoteModel.store_id == store_id,
        )
    )
    return rows.scalar_one_or_none()

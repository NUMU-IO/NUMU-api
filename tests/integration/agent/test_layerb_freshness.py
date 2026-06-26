"""US3 — Layer-B freshness & retire (SC-006, FR-007).

Editing a note re-embeds it (the answer reflects the update); retiring a note
removes it from Layer B so it stops surfacing.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.knowledge import notes_service
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository


def _tenant_contents(results):
    return " ".join(r["content"] for r in results if r["doc"]["layer"] == "tenant")


@pytest.mark.asyncio
async def test_edit_is_reflected_and_retire_removes(test_session):
    tenant, store, staff = uuid4(), uuid4(), uuid4()
    note, _ = await notes_service.create_note(
        test_session,
        tenant_id=tenant,
        store_id=store,
        staff_id=staff,
        title="Return policy",
        body="We accept returns within 14 days.",
        locale="en",
    )

    repo = KnowledgeRepository(test_session)
    embedder = get_embedder()
    q = await embedder.embed_query("return policy days")

    res1 = await repo.search(q, tenant_id=tenant, k=10)
    assert "14 days" in _tenant_contents(res1)

    # Edit → re-embed → reflects the update (SC-006).
    await notes_service.update_note(
        test_session,
        tenant_id=tenant,
        store_id=store,
        staff_id=staff,
        note_id=note.id,
        body="We accept returns within 30 days.",
    )
    res2 = await repo.search(q, tenant_id=tenant, k=10)
    contents2 = _tenant_contents(res2)
    assert "30 days" in contents2
    assert "14 days" not in contents2  # old content replaced

    # Retire → leaves Layer B (FR-007).
    await notes_service.set_note_status(
        test_session,
        tenant_id=tenant,
        store_id=store,
        staff_id=staff,
        note_id=note.id,
        status="retired",
    )
    res3 = await repo.search(q, tenant_id=tenant, k=10)
    assert _tenant_contents(res3) == ""  # no longer surfaced

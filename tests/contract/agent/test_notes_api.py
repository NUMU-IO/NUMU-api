"""US3 — merchant notes contract (FR-004a, Constitution I/III).

Drives the notes service (the route layer only adds auth/RBAC, exercised by
get_agent_context elsewhere) to assert: notes are tenant + store scoped (a wrong
tenant/store can't fetch or mutate), and every mutation writes an audit record.
Runs on SQLite — isolation here is the application-level tenant filter.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.application.agent.knowledge import notes_service
from src.infrastructure.agent.persistence.models import AgentAuditLogModel


async def _audit_count(session, tenant_id) -> int:
    rows = await session.execute(
        select(func.count())
        .select_from(AgentAuditLogModel)
        .where(AgentAuditLogModel.tenant_id == tenant_id)
    )
    return rows.scalar_one()


@pytest.mark.asyncio
async def test_create_writes_audit_and_indexes(test_session):
    tenant, store, staff = uuid4(), uuid4(), uuid4()
    before = await _audit_count(test_session, tenant)
    note, audit_id = await notes_service.create_note(
        test_session,
        tenant_id=tenant,
        store_id=store,
        staff_id=staff,
        title="Shipping FAQ",
        body="We ship nationwide in 2-4 days.",
        locale="en",
    )
    assert note.status == "published"
    assert note.layer_b_doc_id is not None  # indexed into Layer B
    assert audit_id is not None
    assert await _audit_count(test_session, tenant) == before + 1  # audited


@pytest.mark.asyncio
async def test_other_tenant_cannot_fetch_or_mutate(test_session):
    tenant_a, store_a, staff = uuid4(), uuid4(), uuid4()
    note, _ = await notes_service.create_note(
        test_session,
        tenant_id=tenant_a,
        store_id=store_a,
        staff_id=staff,
        title="A note",
        body="Tenant A only.",
        locale="en",
    )

    # A different tenant cannot update or retire tenant A's note.
    other_tenant, other_store = uuid4(), uuid4()
    updated, _ = await notes_service.update_note(
        test_session,
        tenant_id=other_tenant,
        store_id=other_store,
        staff_id=uuid4(),
        note_id=note.id,
        body="hijack",
    )
    assert updated is None  # not found under the wrong tenant/store

    retired, _ = await notes_service.set_note_status(
        test_session,
        tenant_id=other_tenant,
        store_id=other_store,
        staff_id=uuid4(),
        note_id=note.id,
        status="retired",
    )
    assert retired is None


@pytest.mark.asyncio
async def test_list_is_scoped_to_tenant_and_store(test_session):
    tenant, store, staff = uuid4(), uuid4(), uuid4()
    await notes_service.create_note(
        test_session,
        tenant_id=tenant,
        store_id=store,
        staff_id=staff,
        title="N1",
        body="b1",
        locale="en",
    )
    # Same tenant, different store → not listed.
    await notes_service.create_note(
        test_session,
        tenant_id=tenant,
        store_id=uuid4(),
        staff_id=staff,
        title="N2",
        body="b2",
        locale="en",
    )
    notes = await notes_service.list_notes(
        test_session, tenant_id=tenant, store_id=store
    )
    assert [n.title for n in notes] == ["N1"]

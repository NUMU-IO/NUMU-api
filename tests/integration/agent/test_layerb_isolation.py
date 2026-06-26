"""US3 — Layer-B cross-tenant isolation (MANDATORY, SC-005).

Two tenants each author distinct notes → each retrieves only their own; neither
tenant's query ever returns the other tenant's content. Uses the deterministic
fallback embedder + the dual-path repository (JSONB path in tests).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.knowledge import notes_service
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository


@pytest.mark.asyncio
async def test_tenant_b_content_never_leaks_to_tenant_a(test_session):
    tenant_a, store_a = uuid4(), uuid4()
    tenant_b, store_b = uuid4(), uuid4()

    await notes_service.create_note(
        test_session,
        tenant_id=tenant_a,
        store_id=store_a,
        staff_id=uuid4(),
        title="Tenant A return policy",
        body="Tenant A accepts returns within 14 days.",
        locale="en",
    )
    await notes_service.create_note(
        test_session,
        tenant_id=tenant_b,
        store_id=store_b,
        staff_id=uuid4(),
        title="Tenant B return policy",
        body="Tenant B accepts returns within 30 days.",
        locale="en",
    )

    embedder = get_embedder()
    repo = KnowledgeRepository(test_session)
    q = await embedder.embed_query("what is my return policy")

    res_a = await repo.search(q, tenant_id=tenant_a, k=10)
    res_b = await repo.search(q, tenant_id=tenant_b, k=10)

    a_tenant_docs = [r for r in res_a if r["doc"]["layer"] == "tenant"]
    b_tenant_docs = [r for r in res_b if r["doc"]["layer"] == "tenant"]

    # Each tenant sees only their own note; the other's text never appears.
    assert all("Tenant A" in r["content"] for r in a_tenant_docs)
    assert all(
        "30 days" not in r["content"] for r in res_a
    )  # B's content absent from A
    assert all("Tenant B" in r["content"] for r in b_tenant_docs)
    assert all(
        "14 days" not in r["content"] for r in res_b
    )  # A's content absent from B


@pytest.mark.asyncio
async def test_tenant_with_no_layerb_returns_only_shared(test_session):
    repo = KnowledgeRepository(test_session)
    q = await get_embedder().embed_query("anything")
    res = await repo.search(q, tenant_id=uuid4(), k=5)
    assert all(
        r["doc"]["layer"] == "shared" for r in res
    )  # graceful, no other tenant's data

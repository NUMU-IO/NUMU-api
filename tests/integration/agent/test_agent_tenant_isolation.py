"""US1 cross-tenant isolation (spec SC-004, Constitution I).

The harness runs on SQLite (no Postgres RLS), so this exercises the
*defense-in-depth* layer the Agent adds on top of RLS: every agent repository
filters by the active tenant context. A second tenant must never see the first
tenant's conversations.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.agent.entities import Conversation, Turn, TurnRole
from src.infrastructure.agent.persistence.repositories import (
    ConversationRepository,
    TurnRepository,
)
from src.infrastructure.database.connection import set_tenant_id


@pytest.mark.asyncio
async def test_second_tenant_cannot_see_first_tenants_conversations(test_session):
    tenant_a, tenant_b, staff = uuid4(), uuid4(), uuid4()
    conv_repo = ConversationRepository(test_session)
    turn_repo = TurnRepository(test_session)

    try:
        # Tenant A creates a conversation with a turn.
        set_tenant_id(tenant_a)
        conv_a = await conv_repo.create(
            Conversation(id=uuid4(), tenant_id=tenant_a, staff_id=staff)
        )
        await turn_repo.add(
            Turn(
                id=uuid4(),
                tenant_id=tenant_a,
                conversation_id=conv_a.id,
                role=TurnRole.USER,
                content="How many orders today?",
            )
        )

        # Tenant B must not see it — by id or by listing.
        set_tenant_id(tenant_b)
        assert await conv_repo.get(conv_a.id) is None
        assert await conv_repo.list_for_staff(staff) == []
        assert await turn_repo.list_for_conversation(conv_a.id) == []

        # Sanity: tenant A still sees its own.
        set_tenant_id(tenant_a)
        assert await conv_repo.get(conv_a.id) is not None
        assert len(await turn_repo.list_for_conversation(conv_a.id)) == 1
    finally:
        set_tenant_id(None)


@pytest.mark.asyncio
async def test_repository_fails_closed_without_tenant_context(test_session):
    """No tenant in context → repo refuses rather than leaking across tenants."""
    set_tenant_id(None)
    repo = ConversationRepository(test_session)
    with pytest.raises(PermissionError):
        await repo.create(Conversation(id=uuid4(), tenant_id=uuid4(), staff_id=uuid4()))

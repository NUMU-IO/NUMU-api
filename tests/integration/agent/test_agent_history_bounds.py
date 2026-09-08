"""Conversation history is capped at the query, not at the caller.

Every turn re-sends the history to the model, so an uncapped thread costs more
on each message until it exceeds the context window and stops working.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.core.agent.entities import Conversation, Turn, TurnRole
from src.infrastructure.agent.persistence.repositories import (
    ConversationRepository,
    TurnRepository,
)
from src.infrastructure.database.connection import set_tenant_id


@pytest.mark.asyncio
async def test_only_the_most_recent_turns_come_back_and_stay_in_order(test_session):
    tenant_id, staff_id = uuid4(), uuid4()
    try:
        set_tenant_id(tenant_id)
        conv = await ConversationRepository(test_session).create(
            Conversation(id=uuid4(), tenant_id=tenant_id, staff_id=staff_id)
        )
        repo = TurnRepository(test_session)
        # Explicit timestamps: the column's server default has one-second
        # resolution on SQLite, so a burst of inserts would otherwise tie and
        # "the most recent four" would be decided by the tiebreak alone.
        start = datetime.now(UTC) - timedelta(minutes=30)
        for i in range(12):
            await repo.add(
                Turn(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    conversation_id=conv.id,
                    role=TurnRole.USER,
                    content=f"message {i}",
                    created_at=start + timedelta(minutes=i),
                )
            )

        # Newest kept, but replayed oldest-first so the model reads a coherent
        # conversation rather than a reversed one.
        capped = await repo.list_for_conversation(conv.id, limit=4)
        assert [t.content for t in capped] == [
            "message 8",
            "message 9",
            "message 10",
            "message 11",
        ]

        # Without a limit the full thread is still available — the cap is the
        # model's context budget, not a retention policy.
        assert len(await repo.list_for_conversation(conv.id)) == 12
    finally:
        set_tenant_id(None)

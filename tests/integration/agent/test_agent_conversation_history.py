"""Conversation history — auto-title on create + owner-only turn access.

The panel's history UI needs: a human-scannable title (from the opening
message), and a turns endpoint that only the thread's owner can read.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.application.agent.run_turn import stream_turn
from src.application.agent.tools import ToolRegistry
from src.core.agent.interfaces import LLMResponse
from src.infrastructure.agent.persistence.repositories import ConversationRepository
from src.infrastructure.database.connection import set_tenant_id


class _EchoProvider:
    async def chat(self, messages, *, tools=None, model=None, temperature=None):
        return LLMResponse(content="Here you go.", tool_calls=[], model="fake")


async def _title_for(test_session, message: str) -> str | None:
    tenant, store, staff = uuid4(), uuid4(), uuid4()
    set_tenant_id(tenant)
    try:
        events = [
            e
            async for e in stream_turn(
                tenant_id=tenant,
                store_id=store,
                staff_id=staff,
                session=test_session,
                has_permission=None,
                message=message,
                conversation_id=None,
                locale="en",
                provider=_EchoProvider(),
                registry=ToolRegistry(),
            )
        ]
        meta = next(e for e in events if e.type == "meta")
        conv = await ConversationRepository(test_session).get(
            UUID(meta.data["conversation_id"])
        )
        return conv.title
    finally:
        set_tenant_id(None)


@pytest.mark.asyncio
async def test_new_conversation_gets_title_from_first_message(test_session):
    title = await _title_for(
        test_session, "   How   many orders did I get   this week?  "
    )
    # Whitespace collapsed + trimmed.
    assert title == "How many orders did I get this week?"


@pytest.mark.asyncio
async def test_title_is_capped_at_60_chars(test_session):
    title = await _title_for(test_session, "x" * 200)
    assert title is not None and len(title) == 60

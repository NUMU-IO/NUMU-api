"""US1 chat read-path contract (spec US1, FR-003/FR-004).

Drives `stream_turn` with an injected scripted provider + in-memory tool (no
network), then asserts the streamed SSE event contract and that the turn is
persisted (user message + grounded agent reply with tool metadata).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.run_turn import stream_turn
from src.application.agent.tools import ToolRegistry, ToolResult, ToolSpec
from src.core.agent.entities import RiskTier, TurnRole
from src.core.agent.interfaces import LLMResponse, ToolCall
from src.infrastructure.agent.persistence.repositories import TurnRepository
from src.infrastructure.database.connection import set_tenant_id


class ScriptedProvider:
    def __init__(self, script):
        self._script = script
        self._i = 0

    async def chat(self, messages, *, tools=None, model=None, temperature=None):
        resp = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return resp


def _orders_registry():
    async def _exec(ctx, args):
        return ToolResult(ok=True, data={"order_count": 3, "period": "today"})

    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="get_orders",
            description="orders",
            input_schema={"type": "object", "properties": {}},
            risk_tier=RiskTier.AUTO,
            required_permission=None,
            executor=_exec,
        )
    )
    return reg


async def _allow(_code: str) -> bool:
    return True


@pytest.mark.asyncio
async def test_chat_read_streams_events_and_persists_turn(test_session):
    tenant_id, store_id, staff_id = uuid4(), uuid4(), uuid4()
    provider = ScriptedProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="1", name="get_orders", arguments={})],
            model="fake-model",
        ),
        LLMResponse(
            content="You have 3 orders today.", tool_calls=[], model="fake-model"
        ),
    ])

    try:
        set_tenant_id(tenant_id)
        events = []
        async for ev in stream_turn(
            tenant_id=tenant_id,
            store_id=store_id,
            staff_id=staff_id,
            session=test_session,
            has_permission=_allow,
            message="How many orders did I get today?",
            conversation_id=None,
            locale="en",
            provider=provider,
            registry=_orders_registry(),
        ):
            events.append(ev)

        types = [e.type for e in events]
        # SSE contract: meta first, tool call + result, a message, then done.
        assert types[0] == "meta"
        assert "tool_call" in types and "tool_result" in types
        assert "message" in types and types[-1] == "done"

        conversation_id = None
        for e in events:
            if e.type == "meta":
                conversation_id = e.data["conversation_id"]
        assert conversation_id is not None

        # Persistence: user message + grounded agent reply with tool metadata.
        from uuid import UUID

        turns = await TurnRepository(test_session).list_for_conversation(
            UUID(conversation_id)
        )
        assert [t.role for t in turns] == [TurnRole.USER, TurnRole.AGENT]
        agent_turn = turns[1]
        assert agent_turn.content == "You have 3 orders today."
        assert agent_turn.model_used == "fake-model"
        assert any(tc.name == "get_orders" and tc.ok for tc in agent_turn.tool_calls)
    finally:
        set_tenant_id(None)

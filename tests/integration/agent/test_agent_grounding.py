"""US1 grounding / no-hallucination plumbing (spec FR-006, Constitution II).

These are offline, deterministic tests of the agent loop: a scripted LLM and
in-memory tools. They assert the *structure* that makes the no-hallucination
guarantee possible — the model only receives store facts via tool results, and a
tool that returns "unavailable" is surfaced as such (never fabricated).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from src.application.agent.agent_loop import AgentLoop, AgentRunResult
from src.application.agent.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec
from src.core.agent.entities import RiskTier
from src.core.agent.interfaces import LLMResponse, ToolCall


class ScriptedProvider:
    def __init__(self, script):
        self._script = script
        self._i = 0
        self.calls = []

    async def chat(self, messages, *, tools=None, model=None, temperature=None):
        self.calls.append(list(messages))
        resp = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return resp


def _registry_with(name, result):
    async def _exec(ctx, args):
        return result

    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name=name,
            description="test",
            input_schema={"type": "object", "properties": {}},
            risk_tier=RiskTier.AUTO,
            required_permission=None,
            executor=_exec,
        )
    )
    return reg


def _ctx():
    return ToolContext(
        tenant_id=uuid4(), store_id=uuid4(), staff_id=uuid4(), session=None, locale="en"
    )


async def _run(provider, registry):
    loop = AgentLoop(provider, registry, max_iterations=4)
    result = AgentRunResult()
    events = []
    async for ev in loop.run(
        user_message="How many orders today?", history=[], ctx=_ctx(), result=result
    ):
        events.append(ev)
    return events, result


@pytest.mark.asyncio
async def test_unavailable_tool_is_reported_not_fabricated():
    registry = _registry_with("get_orders", ToolResult.unavailable("no data"))
    provider = ScriptedProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="1", name="get_orders", arguments={})],
            model="fake",
        ),
        LLMResponse(
            content="I can't retrieve your orders right now.",
            tool_calls=[],
            model="fake",
        ),
    ])

    events, _result = await _run(provider, registry)
    types = [e.type for e in events]

    assert "tool_call" in types and "message" in types and "done" in types
    # The tool_result surfaced as not-ok (basis for "unavailable", not a guess).
    tool_results = [e for e in events if e.type == "tool_result"]
    assert tool_results and tool_results[0].data["ok"] is False

    # The model's SECOND call must include the tool result as a tool message,
    # carrying ok:false — i.e. the only order info the model saw was the tool's.
    second_call_msgs = provider.calls[1]
    tool_msgs = [m for m in second_call_msgs if m.role == "tool"]
    assert tool_msgs and '"ok": false' in tool_msgs[0].content


@pytest.mark.asyncio
async def test_model_context_has_no_injected_store_facts():
    registry = _registry_with(
        "get_orders", ToolResult(ok=True, data={"order_count": 3})
    )
    provider = ScriptedProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="1", name="get_orders", arguments={})],
            model="fake",
        ),
        LLMResponse(content="You have 3 orders today.", tool_calls=[], model="fake"),
    ])

    _events, result = await _run(provider, registry)

    # First model call: only system + locale + user — no pre-fed assistant/tool
    # data, so the model has no store facts until it calls a tool.
    first_call_msgs = provider.calls[0]
    assert [m.role for m in first_call_msgs] == ["system", "system", "user"]
    assert not any(m.role in ("assistant", "tool") for m in first_call_msgs)
    assert first_call_msgs[-1].content == "How many orders today?"

    # The count reached the model only via the tool-result message.
    second_call_msgs = provider.calls[1]
    tool_msg = next(m for m in second_call_msgs if m.role == "tool")
    assert json.loads(tool_msg.content)["data"]["order_count"] == 3
    assert result.reply_text == "You have 3 orders today."

"""What a turn cost is recorded, not guessed.

A turn is up to `max_iterations` model calls, so the cost of the turn is all
of them summed — not the last response's counts, which is what a naive read of
`LLMResponse` would give you.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.agent_loop import AgentLoop, AgentRunResult
from src.application.agent.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec
from src.core.agent.entities import RiskTier
from src.core.agent.interfaces import LLMProviderError, LLMResponse, ToolCall


class _TwoCallProvider:
    """A tool call, then an answer — two completions, both billed."""

    def __init__(self):
        self.n = 0

    async def chat(self, messages, *, tools=None, model=None, temperature=None):
        self.n += 1
        if self.n == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="1", name="peek", arguments={})],
                model="fake",
                prompt_tokens=100,
                completion_tokens=10,
            )
        return LLMResponse(
            content="done",
            tool_calls=[],
            model="fake",
            prompt_tokens=150,
            completion_tokens=25,
        )


def _registry():
    async def executor(ctx, args):
        return ToolResult(ok=True, data={"ok": True}, source=[])

    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="peek",
            description="d",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            risk_tier=RiskTier.AUTO,
            required_permission=None,
            executor=executor,
        )
    )
    return reg


def _ctx():
    async def allow(_c):
        return True

    return ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=None,
        locale="en",
        has_permission=allow,
    )


@pytest.mark.asyncio
async def test_tokens_are_summed_across_every_call_in_the_turn():
    result = AgentRunResult()
    loop = AgentLoop(_TwoCallProvider(), _registry())
    async for _ in loop.run(user_message="hi", history=[], ctx=_ctx(), result=result):
        pass

    assert result.prompt_tokens == 250  # 100 + 150, not just the last one
    assert result.completion_tokens == 35


@pytest.mark.asyncio
async def test_a_provider_that_reports_nothing_does_not_break_the_turn():
    class _Silent:
        async def chat(self, *a, **kw):
            return LLMResponse(content="ok", tool_calls=[], model="fake")

    result = AgentRunResult()
    loop = AgentLoop(_Silent(), _registry())
    async for _ in loop.run(user_message="hi", history=[], ctx=_ctx(), result=result):
        pass
    assert result.prompt_tokens == 0
    assert result.reply_text == "ok"


@pytest.mark.asyncio
async def test_auth_failure_raises_an_alert_not_just_a_log(monkeypatch):
    """auth and credits take the assistant down for everyone until a human acts."""
    from src.application.agent import agent_loop as mod

    alerted: list[tuple[str, dict]] = []

    class _Logger:
        # The real Log uses slots, so its methods cannot be patched in place;
        # swap the whole object instead.
        def alert(self, event, **kw):
            alerted.append((event, kw))

        def warning(self, *a, **kw):
            pass

        def info(self, *a, **kw):
            pass

    monkeypatch.setattr(mod, "logger", _Logger())

    class _Dead:
        async def chat(self, *a, **kw):
            raise LLMProviderError("bad key", kind="auth")

    async for _ in AgentLoop(_Dead(), _registry()).run(
        user_message="hi", history=[], ctx=_ctx(), result=AgentRunResult()
    ):
        pass

    assert alerted and alerted[0][0] == "agent_llm_unavailable"
    assert alerted[0][1]["failure"] == "auth"


@pytest.mark.asyncio
async def test_an_upstream_blip_is_only_a_warning(monkeypatch):
    """Nobody needs paging because the provider had a bad second."""
    from src.application.agent import agent_loop as mod

    alerted = []

    class _Logger:
        def alert(self, event, **kw):
            alerted.append(event)

        def warning(self, *a, **kw):
            pass

        def info(self, *a, **kw):
            pass

    monkeypatch.setattr(mod, "logger", _Logger())

    class _Flaky:
        async def chat(self, *a, **kw):
            raise LLMProviderError("boom", kind="upstream")

    async for _ in AgentLoop(_Flaky(), _registry()).run(
        user_message="hi", history=[], ctx=_ctx(), result=AgentRunResult()
    ):
        pass
    assert alerted == []

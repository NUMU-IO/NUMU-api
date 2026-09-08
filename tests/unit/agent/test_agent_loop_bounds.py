"""The loop's cost bounds: history, tool-result size, daily turns, error kinds.

None of these fire today — the agent has no API key in production. All of them
fire on the first busy day after it gets one.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from src.application.agent.agent_loop import AgentLoop, AgentRunResult
from src.application.agent.quota import consume_turn
from src.application.agent.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec
from src.core.agent.entities import RiskTier
from src.core.agent.interfaces import (
    ChatMessage,
    LLMProviderError,
    LLMResponse,
    ToolCall,
)
from src.infrastructure.agent.llm.provider import _error_kind


class RecordingProvider:
    """Returns one tool call, then a plain answer. Keeps every prompt it saw."""

    def __init__(self):
        self.calls: list[list[ChatMessage]] = []

    async def chat(self, messages, *, tools=None, model=None, temperature=None):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="1", name="big_read", arguments={})],
                model="fake",
            )
        return LLMResponse(content="done", tool_calls=[], model="fake")


class FailingProvider:
    def __init__(self, kind):
        self._kind = kind

    async def chat(self, *a, **kw):
        raise LLMProviderError("nope", kind=self._kind)


def _registry(payload):
    async def executor(ctx, args):
        return ToolResult(ok=True, data=payload, source=["test"])

    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="big_read",
            description="returns a lot",
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
    async def allow(_code):
        return True

    return ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=None,
        locale="en",
        has_permission=allow,
    )


class TestToolResultsAreBounded:
    @pytest.mark.asyncio
    async def test_a_huge_result_is_truncated_and_says_so(self):
        provider = RecordingProvider()
        loop = AgentLoop(
            provider,
            _registry({"products": ["x" * 100 for _ in range(200)]}),
            tool_result_max_chars=500,
        )
        async for _ in loop.run(
            user_message="list everything",
            history=[],
            ctx=_ctx(),
            result=AgentRunResult(),
        ):
            pass

        tool_msg = [m for m in provider.calls[1] if m.role == "tool"][0]
        assert len(tool_msg.content) < 1500
        body = json.loads(tool_msg.content)
        # Announced, not silent: a model that cannot tell it saw a slice will
        # report the slice as the whole answer.
        assert body["truncated"] is True
        assert "narrow the request" in body["note"]

    @pytest.mark.asyncio
    async def test_a_small_result_is_passed_through_whole(self):
        provider = RecordingProvider()
        loop = AgentLoop(provider, _registry({"count": 3}), tool_result_max_chars=4000)
        async for _ in loop.run(
            user_message="how many",
            history=[],
            ctx=_ctx(),
            result=AgentRunResult(),
        ):
            pass

        body = json.loads([m for m in provider.calls[1] if m.role == "tool"][0].content)
        assert body["data"] == {"count": 3}
        assert "truncated" not in body


class TestProviderErrorsAreClassified:
    def test_status_maps_to_the_cause_a_human_acts_on(self):
        assert _error_kind(401) == "auth"
        assert _error_kind(403) == "auth"
        assert _error_kind(402) == "credits"
        assert _error_kind(500) == "upstream"
        assert _error_kind(400) == "upstream"

    @pytest.mark.asyncio
    async def test_the_kind_reaches_the_stream(self):
        loop = AgentLoop(FailingProvider("auth"), _registry({}))
        events = [
            e
            async for e in loop.run(
                user_message="hi", history=[], ctx=_ctx(), result=AgentRunResult()
            )
        ]
        err = [e for e in events if e.type == "error"][0]
        assert err.data["kind"] == "auth"
        # The merchant cannot fix a dead key; they get one calm sentence.
        assert "unavailable" in err.data["message"]


class FakeCache:
    def __init__(self, *, broken=False):
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.broken = broken

    async def increment(self, key, amount=1):
        if self.broken:
            return 0  # what RedisCacheService returns when Redis is unreachable
        self.counts[key] = self.counts.get(key, 0) + amount
        return self.counts[key]

    async def set(self, key, value, expire=None):
        self.expires[key] = expire


class TestDailyTurnCap:
    @pytest.mark.asyncio
    async def test_allows_up_to_the_limit_then_refuses(self):
        cache, store = FakeCache(), uuid4()
        assert [await consume_turn(cache, store, limit=3) for _ in range(3)] == [
            True,
            True,
            True,
        ]
        assert await consume_turn(cache, store, limit=3) is False

    @pytest.mark.asyncio
    async def test_the_counter_is_given_a_ttl_on_first_use(self):
        cache, store = FakeCache(), uuid4()
        await consume_turn(cache, store, limit=10)
        assert set(cache.expires.values()) == {24 * 60 * 60}

    @pytest.mark.asyncio
    async def test_zero_disables_the_cap(self):
        cache, store = FakeCache(), uuid4()
        for _ in range(50):
            assert await consume_turn(cache, store, limit=0) is True
        assert cache.counts == {}  # not even counted

    @pytest.mark.asyncio
    async def test_a_broken_cache_fails_open(self):
        """Redis is a cache here, not a ledger. Losing it must not close the agent."""
        assert await consume_turn(FakeCache(broken=True), uuid4(), limit=1) is True


class TestRetryPolicy:
    """Transient upstream faults get another attempt; auth and credits do not."""

    class _Flaky:
        def __init__(self, fail_times, kind, retryable):
            self.left = fail_times
            self.kind = kind
            self.retryable = retryable
            self.attempts = 0

        async def chat(self, *a, **kw):
            self.attempts += 1
            if self.left > 0:
                self.left -= 1
                raise LLMProviderError("boom", kind=self.kind, retryable=self.retryable)
            return LLMResponse(content="ok", tool_calls=[], model="fake")

    @pytest.mark.asyncio
    async def test_a_503_is_retried(self):
        from src.infrastructure.agent.llm.provider import RetryingLLMProvider

        inner = self._Flaky(1, "upstream", True)
        out = await RetryingLLMProvider(
            inner, max_retries=2, backoff_seconds=0
        ).chat([])
        assert out.content == "ok"
        assert inner.attempts == 2

    @pytest.mark.asyncio
    async def test_auth_is_not_retried(self):
        """Nothing changes between attempts except how long the merchant waits."""
        from src.infrastructure.agent.llm.provider import RetryingLLMProvider

        inner = self._Flaky(1, "auth", False)
        with pytest.raises(LLMProviderError):
            await RetryingLLMProvider(inner, max_retries=3, backoff_seconds=0).chat([])
        assert inner.attempts == 1

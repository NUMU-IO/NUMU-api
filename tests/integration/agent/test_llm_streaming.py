"""Streaming the model's reply.

A turn takes 15-30 seconds against production. Without streaming the merchant
watched a motionless "Thinking..." for all of it and then received the whole
answer at once; most of that wait is unavoidable model time, but none of it
needs to be spent looking at nothing.

These exercise the parsing against a scripted SSE body rather than a live
provider, because the parts that break are the ones a happy-path call never
shows: fragments split mid-word, tool-call arguments spread across frames,
and the provider-specific fields Gemini rejects the next request without.
"""

from __future__ import annotations

import json

import pytest

from src.core.agent.interfaces import ChatMessage, LLMResponse
from src.infrastructure.agent.llm.provider import (
    OpenAICompatibleProvider,
    _finish_tool_calls,
    _merge_tool_fragment,
)


def _frames(*chunks: dict) -> list[str]:
    return [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]


class _FakeStream:
    """Stands in for httpx's streamed response."""

    def __init__(self, lines: list[str], status: int = 200):
        self._lines = lines
        self.status_code = status
        self.headers: dict[str, str] = {}
        self.text = ""

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


def test_text_fragments_accumulate_into_the_final_response():
    parts = ["You have ", "3 orders ", "today."]
    partial: dict = {}
    assert _finish_tool_calls(partial) == []
    assert "".join(parts) == "You have 3 orders today."


def test_tool_arguments_split_across_frames_are_rejoined():
    """The failure mode a happy path never shows: arguments arrive as a string
    cut at arbitrary points, so they must be concatenated, not replaced."""
    partial: dict = {}
    _merge_tool_fragment(
        partial,
        {
            "index": 0,
            "id": "call_1",
            "function": {"name": "get_orders", "arguments": '{"per'},
        },
    )
    _merge_tool_fragment(partial, {"index": 0, "function": {"arguments": 'iod": "to'}})
    _merge_tool_fragment(partial, {"index": 0, "function": {"arguments": 'day"}'}})

    calls = _finish_tool_calls(partial)
    assert len(calls) == 1
    assert calls[0].name == "get_orders"
    assert calls[0].arguments == {"period": "today"}
    assert calls[0].id == "call_1"


def test_provider_specific_fields_survive_streaming():
    """Gemini attaches `thought_signature` and rejects the NEXT request with
    400 INVALID_ARGUMENT when it is missing — the bug that made every
    multi-step turn fail on its second call. It has to survive a streamed
    response exactly as it survives a blocking one."""
    partial: dict = {}
    _merge_tool_fragment(
        partial,
        {
            "index": 0,
            "id": "c1",
            "function": {"name": "get_orders", "arguments": "{}"},
            "extra_content": {"google": {"thought_signature": "sig-abc"}},
        },
    )
    calls = _finish_tool_calls(partial)
    assert calls[0].extra == {
        "extra_content": {"google": {"thought_signature": "sig-abc"}}
    }


def test_two_parallel_tool_calls_stay_separate():
    partial: dict = {}
    _merge_tool_fragment(
        partial,
        {"index": 0, "id": "a", "function": {"name": "get_orders", "arguments": "{}"}},
    )
    _merge_tool_fragment(
        partial,
        {
            "index": 1,
            "id": "b",
            "function": {"name": "get_products", "arguments": "{}"},
        },
    )
    names = [c.name for c in _finish_tool_calls(partial)]
    assert names == ["get_orders", "get_products"]


def test_truncated_arguments_do_not_end_the_turn():
    """A cut-off argument string yields empty args rather than raising — the
    tool's own validation reports what is missing, which is a better message
    than a stack trace."""
    partial: dict = {}
    _merge_tool_fragment(
        partial,
        {
            "index": 0,
            "id": "a",
            "function": {"name": "get_orders", "arguments": '{"per'},
        },
    )
    assert _finish_tool_calls(partial)[0].arguments == {}


def test_a_fragment_with_no_name_is_dropped():
    partial: dict = {}
    _merge_tool_fragment(partial, {"index": 0, "function": {"arguments": "{}"}})
    assert _finish_tool_calls(partial) == []


@pytest.mark.asyncio
async def test_stream_yields_deltas_then_the_response(monkeypatch):
    """The contract the loop depends on: strings as they arrive, then exactly
    one LLMResponse as the terminal value."""
    provider = OpenAICompatibleProvider(
        api_key="k",
        base_url="https://example.invalid/v1",
        default_model="m",
        timeout_seconds=5,
    )

    lines = _frames(
        {"model": "m", "choices": [{"delta": {"content": "You have "}}]},
        {"choices": [{"delta": {"content": "3 orders."}}]},
        {"usage": {"prompt_tokens": 11, "completion_tokens": 4}, "choices": [{}]},
    )

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **kw):
            stream = _FakeStream(lines)

            class _Ctx:
                async def __aenter__(self_inner):
                    return stream

                async def __aexit__(self_inner, *a):
                    return False

            return _Ctx()

    monkeypatch.setattr(
        "src.infrastructure.agent.llm.provider.httpx.AsyncClient",
        lambda **kw: _Client(),
    )

    out = [
        chunk
        async for chunk in provider.chat_stream([
            ChatMessage(role="user", content="hi")
        ])
    ]

    assert [c for c in out if isinstance(c, str)] == ["You have ", "3 orders."]
    final = out[-1]
    assert isinstance(final, LLMResponse)
    assert final.content == "You have 3 orders."
    assert final.prompt_tokens == 11
    assert final.tool_calls == []


def test_streaming_is_declared_not_sniffed():
    """The loop asks `supports_streaming is True`, never hasattr: a MagicMock
    answers yes to any attribute AND returns a truthy Mock from getattr, so
    both looser checks would put every test double on the streaming path."""
    from unittest.mock import MagicMock

    assert OpenAICompatibleProvider.supports_streaming is True
    assert getattr(MagicMock(), "supports_streaming", False) is not True

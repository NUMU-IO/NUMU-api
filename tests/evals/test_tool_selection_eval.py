"""LLM tool-selection eval — the golden set that de-risks model/prompt changes.

Opt-in (real API calls, costs money): set ``AGENT_EVAL=1`` and the ``AGENT_LLM_*``
env vars, then::

    AGENT_EVAL=1 pytest tests/evals -q

Each case sends ONE real chat-completion with the production system prompt and
the full tool registry, then asserts the model's FIRST tool choice (and, when
specified, an argument subset). Runs at temperature 0 for stability. Skipped
entirely in CI so the suite stays hermetic; run it before merging any change to
the model, the system prompt, or a tool description.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("AGENT_EVAL"),
        reason="LLM eval is opt-in: set AGENT_EVAL=1 (+ AGENT_LLM_* env) to run",
    ),
]

_GOLDEN = json.loads(
    (Path(__file__).parent / "golden_tool_selection.json").read_text(encoding="utf-8")
)
_CASES = _GOLDEN["cases"]


def _provider():
    from src.config import settings as s
    from src.infrastructure.agent.llm.provider import (
        OpenAICompatibleProvider,
        RetryingLLMProvider,
    )

    if not s.agent_llm_api_key:
        pytest.skip("AGENT_LLM_API_KEY not configured")
    inner = OpenAICompatibleProvider(
        base_url=s.agent_llm_base_url,
        api_key=s.agent_llm_api_key,
        default_model=s.agent_llm_model,
        timeout_seconds=s.agent_request_timeout_seconds,
    )
    # Same retry wrapper production uses — free-tier gateways rate-limit a
    # burst of eval cases long before real merchant traffic would.
    return RetryingLLMProvider(inner, max_retries=5, backoff_seconds=5.0)


def _messages(case: dict):
    from src.application.agent.agent_loop import SYSTEM_PROMPT
    from src.core.agent.interfaces import ChatMessage

    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="system", content=f"locale={case['locale']}"),
        ChatMessage(role="user", content=case["message"]),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
async def test_first_tool_choice(case: dict):
    import asyncio

    from src.application.agent.tool_registry import build_default_registry

    # Pace cases so free-tier RPM caps don't 429 the run: at 2s spacing the
    # 12-case burst still exhausted retries mid-suite (each such case then
    # fails even though it passes in isolation). 5s keeps full runs clean.
    await asyncio.sleep(5)
    provider = _provider()
    tools = build_default_registry().openai_tools()
    response = await provider.chat(_messages(case), tools=tools, temperature=0.0)

    called = [tc.name for tc in response.tool_calls]
    if case.get("expect_no_tool"):
        assert called == [], f"expected a plain reply, got tool calls: {called}"
        assert (response.content or "").strip(), "expected a friendly text reply"
        return

    assert called, (
        f"expected a call to one of {case['expect_tools']}, got a plain reply: "
        f"{(response.content or '')[:120]!r}"
    )
    first = response.tool_calls[0]
    assert first.name in case["expect_tools"], (
        f"expected first tool in {case['expect_tools']}, got '{first.name}' "
        f"(all calls: {called})"
    )

    subset = case.get("expect_args_subset")
    if subset and first.name == case["expect_tools"][0]:
        for key, want in subset.items():
            got = first.arguments.get(key)
            assert str(got).upper() == str(want).upper(), (
                f"arg '{key}': expected {want!r}, got {got!r}"
            )

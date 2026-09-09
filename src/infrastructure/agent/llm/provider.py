"""OpenAI-compatible LLM provider for the NUMU Agent.

Default target is Groq (Llama 3.3 70B Versatile) — free/low-cost with
function/tool-calling. Because the wire format is OpenAI-compatible, swapping to
another provider/model is a config change only (Constitution V / FR-012): no
tool or business-logic edits.

Secrets (API key) come from NUMU-api settings and never enter the LLM context,
prompts, client code, or the audit log (Security & Compliance constraints).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from src.config import settings as app_settings
from src.core.agent.interfaces import (
    ChatMessage,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    ToolCall,
)
from src.core.logging import get_logger

logger = get_logger(__name__)


# 502/503/504 are the provider being briefly unwell, not a decision about us.
_RETRYABLE_STATUS = (502, 503, 504)


def _error_kind(status_code: int) -> str:
    """Map an HTTP status to the cause a human would act on."""
    if status_code in (401, 403):
        return "auth"
    if status_code == 402:
        return "credits"
    return "upstream"


def _message_to_wire(msg: ChatMessage) -> dict[str, Any]:
    """Serialize a domain ChatMessage to the OpenAI chat-completions shape."""
    wire: dict[str, Any] = {"role": msg.role}
    # Assistant tool-call request
    if msg.tool_calls:
        wire["content"] = msg.content or ""
        calls = []
        for tc in msg.tool_calls:
            call = {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            # Echo back whatever the provider attached to its own tool call.
            # Gemini returns a thought_signature here and rejects the follow-up
            # request without it, so dropping this breaks every turn that uses
            # a tool at the point where the result is sent back.
            if tc.extra:
                call.update(tc.extra)
            calls.append(call)
        wire["tool_calls"] = calls
        return wire
    # Tool-result message
    if msg.role == "tool":
        wire["content"] = msg.content or ""
        wire["tool_call_id"] = msg.tool_call_id
        if msg.name:
            wire["name"] = msg.name
        return wire
    # A user message carrying images the model can actually see becomes OpenAI
    # content blocks. Gated on `agent_llm_vision` because a text-only model
    # rejects the block form outright — and it does not need it: the URLs are
    # already in the text, which is enough to pass one to a tool.
    if msg.role == "user" and msg.image_urls and app_settings.agent_llm_vision:
        blocks: list[dict[str, Any]] = [{"type": "text", "text": msg.content or ""}]
        blocks += [
            {"type": "image_url", "image_url": {"url": url}} for url in msg.image_urls
        ]
        wire["content"] = blocks
        return wire

    # Plain system/user/assistant message
    wire["content"] = msg.content or ""
    return wire


class OpenAICompatibleProvider:
    #: Declared, not inferred. The loop used to ask `hasattr(provider,
    #: "chat_stream")`, which is True for any MagicMock — so every test double
    #: took the streaming path and the suite went from 79s to 577s. "Has a
    #: method" and "should be streamed to" are different questions.
    supports_streaming = True

    """Thin async client over an OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_seconds: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._timeout = timeout_seconds

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [_message_to_wire(m) for m in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:  # network/timeout
            # A dropped connection or timeout is worth one more attempt.
            raise LLMProviderError(
                f"LLM transport error: {exc}", kind="upstream", retryable=True
            ) from exc

        # Never log the key; _raise_for_status logs status + a trimmed body.
        # `kind` is the field to alert on: auth and credits mean the agent is
        # down for everyone until a human acts, and no retry will help.
        _raise_for_status(resp)

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                parsed = {}
            # Everything that is not part of the OpenAI shape is kept and
            # replayed verbatim; see ToolCall.extra.
            extra = {
                k: v
                for k, v in tc.items()
                if k not in ("id", "type", "function", "index")
            }
            tool_calls.append(
                ToolCall(
                    id=tc.get("id") or fn.get("name", ""),
                    name=fn.get("name", ""),
                    arguments=parsed,
                    extra=extra or None,
                )
            )

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            model=data.get("model"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str | LLMResponse]:
        """Same call as `chat`, delivered as it is produced.

        Yields text deltas as plain strings, then the assembled `LLMResponse`
        as the final item. The caller distinguishes them by type, which keeps
        the signature honest — there is exactly one terminal value and it is
        the same object `chat` would have returned.

        Why this exists: a turn takes 15-30 seconds against production, and
        without streaming the merchant watched a motionless "Thinking..." for
        all of it and then received the whole answer at once. Most of that
        wait is unavoidable model time; none of it needs to be spent looking
        at nothing.

        Tool-call fragments are accumulated too, even though nothing visible
        is streamed for them. A response that turns out to be a tool call
        simply yields no deltas, and the loop carries on as before.
        """
        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [_message_to_wire(m) for m in messages],
            "stream": True,
            # Ask for usage on the final chunk. Providers that ignore this
            # leave the counters at None, which is what they already were.
            "stream_options": {"include_usage": True},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        content_parts: list[str] = []
        # Tool calls arrive in fragments keyed by index, with the name in the
        # first and the arguments spread across many.
        partial: dict[int, dict[str, Any]] = {}
        resolved_model: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status_code >= 400:
                        # The body has not been read yet on a streamed response.
                        await resp.aread()
                        _raise_for_status(resp)

                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            # A malformed frame is not worth ending a turn the
                            # merchant is already waiting on.
                            continue

                        resolved_model = chunk.get("model") or resolved_model
                        if usage := chunk.get("usage"):
                            prompt_tokens = usage.get("prompt_tokens")
                            completion_tokens = usage.get("completion_tokens")

                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}

                        if text := delta.get("content"):
                            content_parts.append(text)
                            yield text

                        for frag in delta.get("tool_calls") or []:
                            _merge_tool_fragment(partial, frag)
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"LLM transport error: {exc}", kind="upstream", retryable=True
            ) from exc

        yield LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=_finish_tool_calls(partial),
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def _raise_for_status(resp) -> None:
    """Turn an error response into the right exception.

    Shared by `chat` and `chat_stream` so a 429 means the same thing on both
    paths — a streamed call that failed differently from a blocking one would
    be a second set of rules to remember.
    """
    if resp.status_code == 429:
        retry_after = resp.headers.get("retry-after")
        raise LLMRateLimitError(
            "LLM provider rate limited",
            retry_after=float(retry_after) if retry_after else None,
        )
    if resp.status_code >= 400:
        kind = _error_kind(resp.status_code)
        logger.warning(
            "agent_llm_error",
            status=resp.status_code,
            kind=kind,
            body=resp.text[:500],
        )
        raise LLMProviderError(
            f"LLM provider returned {resp.status_code}",
            kind=kind,
            retryable=resp.status_code in _RETRYABLE_STATUS,
        )


def _merge_tool_fragment(partial: dict, frag: dict) -> None:
    """Fold one streamed tool-call fragment into the accumulator.

    Arguments arrive as a string split across arbitrarily many frames, so they
    are concatenated rather than replaced. `index` is the only thing tying the
    fragments of one call together; providers that omit it send a single call,
    so 0 is the right default rather than an error.
    """
    idx = frag.get("index", 0)
    slot = partial.setdefault(idx, {"id": None, "name": "", "args": "", "extra": {}})
    if frag.get("id"):
        slot["id"] = frag["id"]
    fn = frag.get("function") or {}
    if fn.get("name"):
        slot["name"] = fn["name"]
    if fn.get("arguments"):
        slot["args"] += fn["arguments"]
    # Provider-specific fields — Gemini's thought_signature among them — must
    # survive streaming exactly as they survive a blocking call, or the next
    # request is rejected. See ToolCall.extra.
    for key, value in frag.items():
        if key not in ("id", "type", "function", "index"):
            slot["extra"][key] = value


def _finish_tool_calls(partial: dict) -> list[ToolCall]:
    """The accumulated fragments as ToolCalls, in the order they arrived."""
    calls: list[ToolCall] = []
    for idx in sorted(partial):
        slot = partial[idx]
        if not slot["name"]:
            continue
        try:
            args = json.loads(slot["args"]) if slot["args"] else {}
        except json.JSONDecodeError:
            # A truncated argument string is not worth failing the turn over;
            # the tool's own validation reports what is missing.
            args = {}
        calls.append(
            ToolCall(
                id=slot["id"] or slot["name"],
                name=slot["name"],
                arguments=args,
                extra=slot["extra"] or None,
            )
        )
    return calls


class RetryingLLMProvider:
    supports_streaming = True

    """Wraps a provider with bounded backoff on 429 (FR-013).

    Free-tier rate limits are a first-class concern: instead of failing the
    merchant hard or fabricating output, retry a few times with backoff. The
    heavier durable queue (Celery/Redis) is reserved for bulk/multi-step work.
    """

    def __init__(
        self,
        inner: OpenAICompatibleProvider,
        *,
        max_retries: int,
        backoff_seconds: float,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._backoff = backoff_seconds

    async def chat(
        self, messages, *, tools=None, model=None, temperature=None
    ) -> LLMResponse:
        attempt = 0
        while True:
            try:
                return await self._inner.chat(
                    messages, tools=tools, model=model, temperature=temperature
                )
            except LLMRateLimitError as exc:
                attempt += 1
                if attempt > self._max_retries:
                    raise
                delay = exc.retry_after or (self._backoff * attempt)
                logger.info(
                    "agent_llm_rate_limited_retry", attempt=attempt, delay=delay
                )
                await asyncio.sleep(delay)
            except LLMProviderError as exc:
                # Only the transient upstream faults. auth and credits fall
                # straight through: nothing changes between attempts except
                # how long the merchant waits for the same failure.
                if not getattr(exc, "retryable", False):
                    raise
                attempt += 1
                if attempt > self._max_retries:
                    raise
                delay = self._backoff * attempt
                logger.info(
                    "agent_llm_upstream_retry",
                    attempt=attempt,
                    delay=delay,
                    kind=exc.kind,
                )
                await asyncio.sleep(delay)

    async def chat_stream(
        self, messages, *, tools=None, model=None, temperature=None
    ) -> AsyncIterator[str | LLMResponse]:
        """Pass through, deliberately WITHOUT the retry.

        A retry replays a call from the start. On a streamed turn the merchant
        has already been shown the first attempt's text, so a second attempt
        would either duplicate it or contradict it mid-sentence. Better to
        surface the failure than to rewrite what someone is reading.

        The blocking `chat` keeps its retry, and the loop still uses it for
        every call that is not the one being streamed to a person.
        """
        async for chunk in self._inner.chat_stream(
            messages, tools=tools, model=model, temperature=temperature
        ):
            yield chunk


def get_llm_provider():
    """Build the configured provider (default Groq), with retry. Swap via env only."""
    s = app_settings
    inner = OpenAICompatibleProvider(
        base_url=s.agent_llm_base_url,
        api_key=s.agent_llm_api_key,
        default_model=s.agent_llm_model,
        timeout_seconds=s.agent_request_timeout_seconds,
    )
    return RetryingLLMProvider(
        inner,
        max_retries=s.agent_rate_limit_max_retries,
        backoff_seconds=s.agent_rate_limit_backoff_seconds,
    )

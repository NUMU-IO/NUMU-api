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
from typing import Any

import httpx

from src.config import settings as app_settings
from src.config.logging_config import get_logger
from src.core.agent.interfaces import (
    ChatMessage,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    ToolCall,
)

logger = get_logger(__name__)


def _message_to_wire(msg: ChatMessage) -> dict[str, Any]:
    """Serialize a domain ChatMessage to the OpenAI chat-completions shape."""
    wire: dict[str, Any] = {"role": msg.role}
    # Assistant tool-call request
    if msg.tool_calls:
        wire["content"] = msg.content or ""
        wire["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in msg.tool_calls
        ]
        return wire
    # Tool-result message
    if msg.role == "tool":
        wire["content"] = msg.content or ""
        wire["tool_call_id"] = msg.tool_call_id
        if msg.name:
            wire["name"] = msg.name
        return wire
    # Plain system/user/assistant message
    wire["content"] = msg.content or ""
    return wire


class OpenAICompatibleProvider:
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
            raise LLMProviderError(f"LLM transport error: {exc}") from exc

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            raise LLMRateLimitError(
                "LLM provider rate limited",
                retry_after=float(retry_after) if retry_after else None,
            )
        if resp.status_code >= 400:
            # Do not log the key; log status + a trimmed body for diagnostics.
            logger.warning(
                "agent_llm_error", status=resp.status_code, body=resp.text[:500]
            )
            raise LLMProviderError(f"LLM provider returned {resp.status_code}")

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
            tool_calls.append(
                ToolCall(
                    id=tc.get("id") or fn.get("name", ""),
                    name=fn.get("name", ""),
                    arguments=parsed,
                )
            )

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            model=data.get("model"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )


class RetryingLLMProvider:
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

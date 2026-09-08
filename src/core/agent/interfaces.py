"""Domain interfaces for the NUMU Agent (Clean Architecture ports).

Pure typing/protocols — implemented by the infrastructure layer. Keeping the
LLM provider behind a Protocol is what makes the model layer swappable by config
alone (Constitution V / FR-012).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from src.core.agent.entities import (
    ActionProposal,
    AuditRecord,
    Conversation,
    Turn,
)

# ── LLM message / response value types ───────────────────────────────────────


@dataclass
class ChatMessage:
    """One message in the model conversation.

    ``role`` is one of: system | user | assistant | tool. ``tool_calls`` is set
    on an assistant message that requested tools; ``tool_call_id``/``name`` are
    set on a tool-result message.
    """

    role: str
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolCall:
    """A model-requested tool invocation.

    ``extra`` carries provider fields that are not part of the OpenAI schema
    but which the provider requires back when the call is replayed in the
    transcript. Gemini attaches a ``thought_signature`` here and rejects the
    next request with 400 INVALID_ARGUMENT if it is missing, which made every
    multi-step turn fail on the second iteration. Kept opaque on purpose: this
    client speaks one wire format, and a provider-specific field it never
    interprets is cheaper than a second client.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    extra: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    """Result of one model completion."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMRateLimitError(Exception):
    """Raised by a provider on HTTP 429 so the loop can queue/retry (FR-013)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMProviderError(Exception):
    """Non-retryable provider failure (bad request, auth, 5xx after retries).

    ``kind`` separates the causes that need different human responses:

    * ``auth``     — the key is missing, wrong, or revoked. Nobody is coming to
                     fix this on its own; it needs an operator.
    * ``credits``  — the account is out of money or over quota. Also an
                     operator, and retrying is just a slower failure.
    * ``upstream`` — the provider is having a bad day. Worth trying later.

    All three used to arrive as one undifferentiated error, which meant a dead
    API key and a dead provider looked identical in the logs.

    ``retryable`` marks the upstream faults that are worth trying again: a
    502/503/504 or a dropped connection is usually a blip, and one of those
    should not end a merchant's turn. auth and credits are never retryable —
    nothing changes between attempts except the wait.
    """

    def __init__(
        self, message: str, kind: str = "upstream", *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


class LLMProvider(Protocol):
    """OpenAI-compatible chat-completions port with tool-calling support."""

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Run one completion. Raises LLMRateLimitError on 429."""
        ...


# ── Persistence ports ─────────────────────────────────────────────────────────


class ConversationRepository(Protocol):
    async def create(self, conversation: Conversation) -> Conversation: ...
    async def get(self, conversation_id: UUID) -> Conversation | None: ...
    async def list_for_staff(
        self, staff_id: UUID, *, limit: int = 50
    ) -> list[Conversation]: ...


class TurnRepository(Protocol):
    async def add(self, turn: Turn) -> Turn: ...
    async def list_for_conversation(self, conversation_id: UUID) -> list[Turn]: ...


class ProposalRepository(Protocol):
    async def add(self, proposal: ActionProposal) -> ActionProposal: ...
    async def get(self, proposal_id: UUID) -> ActionProposal | None: ...
    async def get_pending_for_conversation(
        self, conversation_id: UUID
    ) -> ActionProposal | None: ...


class AuditRepository(Protocol):
    async def add(self, record: AuditRecord) -> AuditRecord: ...

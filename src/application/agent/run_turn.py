"""`run_turn` — the US1 read-path use case.

Loads/creates the conversation, replays history, runs the agent loop while
streaming events, then persists the user message and the agent reply (with
sanitized tool-call metadata, model, and latency). Tenant scoping is enforced by
the repositories (RLS + explicit tenant filter); the loop never sees another
tenant's data.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from src.application.agent.agent_loop import AgentEvent, AgentLoop, AgentRunResult
from src.application.agent.knowledge.system_map import build_system_map
from src.application.agent.scope import decline_message, off_domain_reason
from src.application.agent.tool_registry import build_default_registry
from src.application.agent.tools import ToolContext
from src.config import settings as app_settings
from src.core.agent.entities import ActionProposal, Conversation, Turn, TurnRole
from src.core.agent.interfaces import ChatMessage
from src.core.logging import get_logger
from src.infrastructure.agent.llm import get_llm_provider
from src.infrastructure.agent.persistence.repositories import (
    ConversationRepository,
    ProposalRepository,
    TurnRepository,
)

logger = get_logger(__name__)


def _history_to_messages(turns: list[Turn]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for t in turns:
        role = "user" if t.role == TurnRole.USER else "assistant"
        messages.append(ChatMessage(role=role, content=t.content))
    return messages


async def stream_turn(
    *,
    tenant_id: UUID,
    store_id: UUID,
    staff_id: UUID,
    session,
    has_permission,
    message: str,
    conversation_id: UUID | None,
    locale: str = "en",
    provider=None,
    registry=None,
) -> AsyncIterator[AgentEvent]:
    """Run one turn, yielding SSE events. Persists user + agent turns at the end.

    ``provider``/``registry`` are injectable for testing; in production they
    default to the configured LLM provider and the default tool registry.
    """
    conv_repo = ConversationRepository(session)
    turn_repo = TurnRepository(session)

    # Resolve or create the conversation (tenant-scoped).
    if conversation_id is not None:
        conversation = await conv_repo.get(conversation_id)
        if conversation is None:
            yield AgentEvent(
                "error", {"code": "not_found", "message": "Conversation not found"}
            )
            return
    else:
        conversation = await conv_repo.create(
            Conversation(id=uuid4(), tenant_id=tenant_id, staff_id=staff_id)
        )

    yield AgentEvent("meta", {"conversation_id": str(conversation.id)})

    # Scope guardrail (Constitution VIII): decline clearly off-domain requests up
    # front — no model call, no open-domain answer.
    reason = off_domain_reason(message)
    if reason:
        decline = decline_message(locale)
        yield AgentEvent("declined", {"reason": reason})
        yield AgentEvent("message", {"text": decline})
        yield AgentEvent("done", {"declined": True})
        await turn_repo.add(
            Turn(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                role=TurnRole.USER,
                content=message,
            )
        )
        await turn_repo.add(
            Turn(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                role=TurnRole.AGENT,
                content=decline,
            )
        )
        return

    history = await turn_repo.list_for_conversation(conversation.id)

    ctx = ToolContext(
        tenant_id=tenant_id,
        store_id=store_id,
        staff_id=staff_id,
        session=session,
        locale=locale,
        has_permission=has_permission,
    )

    loop = AgentLoop(
        provider or get_llm_provider(),
        registry or build_default_registry(),
        max_iterations=app_settings.agent_max_tool_iterations,
        temperature=app_settings.agent_llm_temperature,
    )

    result = AgentRunResult()
    started = time.monotonic()
    try:
        async for event in loop.run(
            user_message=message,
            history=_history_to_messages(history),
            ctx=ctx,
            result=result,
            system_context=build_system_map(locale),
        ):
            yield event
    except Exception as exc:  # noqa: BLE001 — surface, never fabricate
        logger.warning(
            "agent_run_turn_error", error=str(exc), conversation_id=str(conversation.id)
        )
        # The loop already emitted a soft error event; just stop persisting a reply.
        # Still persist the user message so history is consistent.
        await turn_repo.add(
            Turn(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                role=TurnRole.USER,
                content=message,
            )
        )
        return

    latency_ms = int((time.monotonic() - started) * 1000)

    # Gated write (Constitution III): a CONFIRM-tier tool produced a proposal.
    # Persist it and surface it — nothing is applied until /agent/confirm.
    if result.pending_proposal:
        pp = result.pending_proposal
        proposal = await ProposalRepository(session).add(
            ActionProposal(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                tool_name=pp["tool_name"],
                params=pp["params"],
                diff=pp["diff"],
                store_id=store_id,
                based_on_theme_version=pp.get("based_on_theme_version"),
            )
        )
        reply = (
            "راجع التغيير ده وأكّده عشان يتطبّق."
            if locale == "ar"
            else "Review this change and confirm to apply it."
        )
        result.reply_text = reply
        yield AgentEvent(
            "proposal",
            {
                "proposal_id": str(proposal.id),
                "summary": pp.get("summary"),
                "diff": pp["diff"],
            },
        )
        yield AgentEvent("message", {"text": reply})
        yield AgentEvent("done", {"model_used": result.model_used, "proposal": True})

    # Persist user message then the agent reply with sanitized tool metadata.
    await turn_repo.add(
        Turn(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role=TurnRole.USER,
            content=message,
        )
    )
    await turn_repo.add(
        Turn(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role=TurnRole.AGENT,
            content=result.reply_text,
            tool_calls=result.tool_calls,
            model_used=result.model_used,
            latency_ms=latency_ms,
        )
    )

    logger.info(
        "agent_turn_completed",
        conversation_id=str(conversation.id),
        model=result.model_used,
        latency_ms=latency_ms,
        tool_calls=[tc.name for tc in result.tool_calls],
    )

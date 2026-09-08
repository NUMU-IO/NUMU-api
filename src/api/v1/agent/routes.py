"""Agent HTTP routes (store-scoped).

`POST .../agent/chat` streams the turn over SSE (FR-003): typed events the Hub
panel renders as text or, later, a ProposalCard. `GET .../agent/conversations`
lists the caller's threads. Auth/tenant/permission come from `get_agent_context`.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from src.api.v1.agent.deps import AgentRequestContext, get_agent_context
from src.application.agent.proposals import (
    NothingToUndoError,
    PermissionDeniedError,
    ProposalError,
    StaleProposalError,
    apply_proposal,
    decline_proposal,
    undo_last,
)
from src.application.agent.run_turn import stream_turn
from src.core.logging import get_logger
from src.infrastructure.agent.persistence.repositories import (
    AuditRepository,
    ConversationRepository,
)
from src.infrastructure.database.connection import set_tenant_id

logger = get_logger(__name__)

router = APIRouter(prefix="/stores/{store_id}/agent", tags=["Agent"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: UUID | None = None
    locale: str | None = Field(default=None, pattern="^(ar|en)$")


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(
    body: ChatRequest,
    ctx: Annotated[AgentRequestContext, Depends(get_agent_context)],
) -> StreamingResponse:
    locale = body.locale or "en"

    async def event_stream():
        # A StreamingResponse body is iterated AFTER the endpoint returns, in a
        # context where the request-scoped tenant ContextVar (set by the tenant
        # middleware) has already been torn down. The agent repositories resolve
        # the tenant via that ContextVar (get_tenant_id), so without re-seeding it
        # here every persistence call fails closed with "No tenant context".
        # Re-establish it from the authenticated ctx before streaming the turn.
        set_tenant_id(ctx.tenant_id)
        try:
            async for event in stream_turn(
                tenant_id=ctx.tenant_id,
                store_id=ctx.store_id,
                staff_id=ctx.staff_id,
                session=ctx.session,
                has_permission=ctx.has_permission,
                message=body.message,
                conversation_id=body.conversation_id,
                locale=locale,
            ):
                yield _sse(event.type, event.data)
        except Exception as exc:  # noqa: BLE001 — never leak a stacktrace to the stream
            logger.warning("agent_chat_stream_error", error=str(exc))
            yield _sse(
                "error",
                {"code": "internal", "message": "The assistant failed unexpectedly."},
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx proxy buffering for SSE
        },
    )


@router.get("/conversations")
async def list_conversations(
    ctx: Annotated[AgentRequestContext, Depends(get_agent_context)],
) -> dict:
    repo = ConversationRepository(ctx.session)
    conversations = await repo.list_for_staff(ctx.staff_id, limit=50)
    return {
        "conversations": [
            {
                "id": str(c.id),
                "title": c.title,
                "status": c.status.value,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in conversations
        ]
    }


class ConfirmRequest(BaseModel):
    proposal_id: UUID
    decision: Literal["confirm", "decline"] = "confirm"


class UndoRequest(BaseModel):
    conversation_id: UUID


@router.post("/confirm")
async def confirm(
    body: ConfirmRequest,
    ctx: Annotated[AgentRequestContext, Depends(get_agent_context)],
) -> dict:
    if body.decision == "decline":
        return await decline_proposal(ctx.session, proposal_id=body.proposal_id)

    try:
        return await apply_proposal(
            ctx.session,
            store_id=ctx.store_id,
            staff_id=ctx.staff_id,
            tenant_id=ctx.tenant_id,
            conversation_id=None,
            proposal_id=body.proposal_id,
            has_permission=ctx.has_permission,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": exc.message},
        )
    except StaleProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        )
    except ProposalError as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "not_found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=code, detail={"code": exc.code, "message": exc.message}
        )


@router.get("/audit")
async def list_audit(
    ctx: Annotated[AgentRequestContext, Depends(get_agent_context)],
) -> dict:
    """Tenant-scoped audit log of applied/rejected agent writes (admin view)."""
    if not await ctx.has_permission("themes.view"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Missing permission: themes.view"},
        )
    records = await AuditRepository(ctx.session).list_for_tenant(limit=100)
    return {
        "audit": [
            {
                "id": str(r.id),
                "tool_name": r.tool_name,
                "result": r.result.value,
                "staff_id": str(r.staff_id) if r.staff_id else None,
                "conversation_id": str(r.conversation_id)
                if r.conversation_id
                else None,
                "model_used": r.model_used,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    }


@router.post("/undo")
async def undo(
    body: UndoRequest,
    ctx: Annotated[AgentRequestContext, Depends(get_agent_context)],
) -> dict:
    try:
        return await undo_last(
            ctx.session,
            store_id=ctx.store_id,
            staff_id=ctx.staff_id,
            tenant_id=ctx.tenant_id,
            conversation_id=body.conversation_id,
            has_permission=ctx.has_permission,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": exc.message},
        )
    except NothingToUndoError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        )

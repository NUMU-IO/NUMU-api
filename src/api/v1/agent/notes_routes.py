"""US3 — merchant notes/FAQ authoring API (Layer B, FR-004a).

Store-scoped + user-authenticated (reuses `get_agent_context`: JWT + CSRF + tenant
+ RBAC). The caller's tenant_id/staff_id come from the session, never the body. Each
note is tenant-isolated (RLS), embedded into the tenant's Layer B on publish, and
every mutation is audited. Notes authoring maps to the general store-content
permission (`general.edit`); listing requires `general.view`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.v1.agent.deps import AgentRequestContext, get_agent_context
from src.application.agent.knowledge import notes_service
from src.core.logging import get_logger

logger = get_logger(__name__)

_VIEW_PERMISSION = "general.view"
_EDIT_PERMISSION = "general.edit"

router = APIRouter(prefix="/stores/{store_id}/agent/notes", tags=["Agent"])


class NoteIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    body: str = Field(..., min_length=1, max_length=8000)
    locale: str = Field(default="en", pattern="^(ar|en)$")


class NotePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    body: str | None = Field(default=None, min_length=1, max_length=8000)
    locale: str | None = Field(default=None, pattern="^(ar|en)$")


class StatusIn(BaseModel):
    status: str = Field(..., pattern="^(retired|published)$")


async def _require(ctx: AgentRequestContext, permission: str) -> None:
    if ctx.has_permission is not None and not await ctx.has_permission(permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": f"Missing required permission: {permission}",
            },
        )


def _note_out(note) -> dict:
    return {
        "id": str(note.id),
        "title": note.title,
        "locale": note.locale,
        "status": note.status,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


@router.get("")
async def list_notes(
    ctx: Annotated[AgentRequestContext, Depends(get_agent_context)],
) -> dict:
    await _require(ctx, _VIEW_PERMISSION)
    notes = await notes_service.list_notes(
        ctx.session, tenant_id=ctx.tenant_id, store_id=ctx.store_id
    )
    return {"notes": [_note_out(n) for n in notes]}


@router.post("")
async def create_note(
    body: NoteIn, ctx: Annotated[AgentRequestContext, Depends(get_agent_context)]
) -> dict:
    await _require(ctx, _EDIT_PERMISSION)
    note, audit_id = await notes_service.create_note(
        ctx.session,
        tenant_id=ctx.tenant_id,
        store_id=ctx.store_id,
        staff_id=ctx.staff_id,
        title=body.title,
        body=body.body,
        locale=body.locale,
    )
    return {
        "id": str(note.id),
        "status": note.status,
        "layer_b_doc_id": str(note.layer_b_doc_id) if note.layer_b_doc_id else None,
        "audit_id": str(audit_id),
    }


@router.put("/{note_id}")
async def update_note(
    note_id: UUID,
    body: NotePatch,
    ctx: Annotated[AgentRequestContext, Depends(get_agent_context)],
) -> dict:
    await _require(ctx, _EDIT_PERMISSION)
    note, audit_id = await notes_service.update_note(
        ctx.session,
        tenant_id=ctx.tenant_id,
        store_id=ctx.store_id,
        staff_id=ctx.staff_id,
        note_id=note_id,
        title=body.title,
        body=body.body,
        locale=body.locale,
    )
    if note is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Note not found"}
        )
    return {
        "id": str(note.id),
        "status": note.status,
        "layer_b_doc_id": str(note.layer_b_doc_id) if note.layer_b_doc_id else None,
        "audit_id": str(audit_id),
    }


@router.post("/{note_id}/retire")
async def set_status(
    note_id: UUID,
    body: StatusIn,
    ctx: Annotated[AgentRequestContext, Depends(get_agent_context)],
) -> dict:
    await _require(ctx, _EDIT_PERMISSION)
    note, audit_id = await notes_service.set_note_status(
        ctx.session,
        tenant_id=ctx.tenant_id,
        store_id=ctx.store_id,
        staff_id=ctx.staff_id,
        note_id=note_id,
        status=body.status,
    )
    if note is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Note not found"}
        )
    return {"id": str(note.id), "status": note.status, "audit_id": str(audit_id)}

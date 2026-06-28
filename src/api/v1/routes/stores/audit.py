"""MCP action audit routes nested under stores.

URL: /stores/{store_id}/audit

A durable, Postgres-backed audit trail for actions taken by machine clients
(the NUMU MCP server). It records each mutation as an ``mcp.action`` row in the
shared ``audit_logs`` table and supports the MCP's undo feature:

  * POST   /audit                 — record one action
  * GET    /audit                 — list recent actions (optionally undoable-only)
  * POST   /audit/{id}/mark-undone — flag an action as undone

This lets the MCP run fully stateless (e.g. on AWS) while keeping the audit
trail in the same database — and visible in the merchant dashboard — without
ever handing the MCP direct database access.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.audit_service import AuditService
from src.core.entities.store import Store
from src.infrastructure.database.models.audit import AuditLogModel

router = APIRouter(prefix="/{store_id}/audit")

_MCP_EVENT_TYPE = "mcp.action"
# Bounded window we scan when filtering undoable actions in Python (keeps the
# query simple and dialect-agnostic while staying cheap per store).
_SCAN_WINDOW = 200


class AuditCreateRequest(BaseModel):
    """One MCP action to record."""

    action: str = Field(min_length=1, max_length=50, description="Tool name / action.")
    resource_type: str | None = Field(default=None, max_length=50)
    resource_id: str | None = Field(default=None, max_length=100)
    severity: str = Field(default="info", max_length=20)
    details: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form metadata. The MCP stores arguments, result_status, "
            "result_summary, undo_payload and is_undone here."
        ),
    )


def _to_entry(row: AuditLogModel) -> dict[str, Any]:
    """Project a row into the shape the MCP's audit backend expects."""
    details = row.details or {}
    undo_payload = details.get("undo_payload")
    return {
        "id": str(row.id),
        "timestamp": row.created_at.isoformat() if row.created_at else None,
        "tool_name": row.action,
        "arguments": details.get("arguments"),
        "result_status": details.get("result_status"),
        "result_summary": details.get("result_summary"),
        "undo_payload": undo_payload,
        "is_undone": bool(details.get("is_undone")),
        "undoable": bool(undo_payload) and not details.get("is_undone"),
    }


@router.post(
    "/",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Record an MCP action",
    operation_id="create_audit_entry",
)
async def create_audit_entry(
    request: AuditCreateRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Record one machine-client action for this store."""
    service = AuditService(db)
    entry = await service.log(
        event_type=_MCP_EVENT_TYPE,
        action=request.action,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        severity=request.severity,
        user_id=store.owner_id,
        store_id=store.id,
        tenant_id=store.tenant_id,
        details=request.details,
    )
    await db.flush()
    return SuccessResponse(data={"id": str(entry.id)}, message="Audit entry recorded")


@router.get(
    "/",
    response_model=SuccessResponse[list[dict]],
    summary="List recent MCP actions",
    operation_id="list_audit_entries",
)
async def list_audit_entries(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(25, ge=1, le=100),
    undoable: bool = Query(False, description="Return only still-undoable actions."),
):
    """List recent MCP actions for this store, newest first."""
    scan = max(limit, _SCAN_WINDOW) if undoable else limit
    result = await db.execute(
        select(AuditLogModel)
        .where(
            AuditLogModel.store_id == store.id,
            AuditLogModel.tenant_id == store.tenant_id,
            AuditLogModel.event_type == _MCP_EVENT_TYPE,
        )
        .order_by(AuditLogModel.created_at.desc())
        .limit(scan)
    )
    rows = result.scalars().all()
    entries = [_to_entry(r) for r in rows]
    if undoable:
        entries = [
            e
            for e in entries
            if e["undoable"] and (e.get("result_status") == "success")
        ][:limit]
    return SuccessResponse(data=entries)


@router.post(
    "/{audit_id}/mark-undone",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark an MCP action as undone",
    operation_id="mark_audit_entry_undone",
)
async def mark_audit_entry_undone(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    audit_id: Annotated[UUID, Path(description="The audit entry id")],
):
    """Flag a previously recorded action as undone (idempotent)."""
    result = await db.execute(
        select(AuditLogModel).where(
            AuditLogModel.id == audit_id,
            AuditLogModel.store_id == store.id,
            AuditLogModel.tenant_id == store.tenant_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Audit entry not found"
        )
    # Reassign details so SQLAlchemy flags the JSONB column dirty.
    row.details = {**(row.details or {}), "is_undone": True}
    await db.flush()

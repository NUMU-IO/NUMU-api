"""Staff session routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.permissions import require_staff_edit
from src.api.dependencies.tenant import get_current_tenant
from src.infrastructure.database.models.public import TenantModel
from src.infrastructure.repositories.staff_session_repository import (
    StaffSessionRepository,
)

router = APIRouter(prefix="/staff/sessions", tags=["Staff - Sessions"])


@router.get("")
async def list_sessions(
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
):
    """List active staff sessions."""
    repo = StaffSessionRepository(db)
    sessions = await repo.get_active_by_tenant(tenant.id)

    return {
        "sessions": [
            {
                "id": str(s.id),
                "membership_id": str(s.membership_id),
                "ip_address": s.ip,
                "user_agent": s.user_agent,
                "created_at": s.created_at.isoformat(),
                "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            }
            for s in sessions
        ]
    }


@router.post("/{session_id}/revoke")
async def revoke_session(
    session_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Revoke a staff session."""
    repo = StaffSessionRepository(db)
    revoked = await repo.revoke(session_id, user_id)

    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return {"status": "revoked"}


@router.post("/revoke-all")
async def revoke_all_sessions(
    target_membership_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Revoke all sessions for a membership."""
    repo = StaffSessionRepository(db)
    count = await repo.revoke_by_membership(target_membership_id, user_id)

    return {"status": "revoked", "count": count}

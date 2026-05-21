"""Membership override routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.permissions import require_staff_edit
from src.infrastructure.database.models.public.membership_override import (
    OverrideEffect,
)
from src.infrastructure.repositories.override_repository import OverrideRepository

router = APIRouter(prefix="/staff/overrides", tags=["Staff - Overrides"])


@router.get("")
async def list_overrides(
    membership_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all overrides for a membership."""
    repo = OverrideRepository(db)
    overrides = await repo.get_by_membership(membership_id)

    return {
        "overrides": [
            {
                "id": str(o.id),
                "permission_id": str(o.permission_id),
                "effect": o.effect.value,
                "reason": o.reason,
                "expires_at": o.expires_at.isoformat() if o.expires_at else None,
                "created_at": o.created_at.isoformat(),
            }
            for o in overrides
        ]
    }


@router.post("")
async def set_override(
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    membership_id: UUID = Body(..., embed=True),
    permission_id: UUID = Body(..., embed=True),
    effect: str = Body(..., embed=True),
    reason: str | None = Body(None, embed=True),
    expires_at: str | None = Body(None, embed=True),
):
    """Set a permission override for a membership."""
    from datetime import datetime

    try:
        effect_enum = OverrideEffect(effect)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid effect. Must be 'allow' or 'deny'",
        )

    expires_dt = None
    if expires_at:
        expires_dt = datetime.fromisoformat(expires_at)

    repo = OverrideRepository(db)
    override = await repo.set_override(
        membership_id=membership_id,
        permission_id=permission_id,
        effect=effect_enum,
        granted_by_id=user_id,
        reason=reason,
        expires_at=expires_dt,
    )

    return {
        "id": str(override.id),
        "status": "set",
    }


@router.delete("/{permission_id}")
async def clear_override(
    permission_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    membership_id: UUID = Query(...),
):
    """Clear a permission override."""
    repo = OverrideRepository(db)
    deleted = await repo.clear_override(membership_id, permission_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Override not found",
        )

    return {"status": "cleared"}

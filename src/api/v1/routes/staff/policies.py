"""Staff access policy routes (IP allowlist, working hours)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.permissions import require_staff_edit

router = APIRouter(prefix="/staff/policies", tags=["Staff - Policies"])


@router.get("")
async def list_policies(
    membership_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List access policy for a membership."""
    from sqlalchemy import select

    from src.infrastructure.database.models.public.staff_access_policy import (
        StaffAccessPolicyModel,
    )

    result = await db.execute(
        select(StaffAccessPolicyModel).where(
            StaffAccessPolicyModel.membership_id == membership_id,
        )
    )
    policy = result.scalar_one_or_none()

    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Policy not found",
        )

    return {
        "id": str(policy.id),
        "membership_id": str(policy.membership_id),
        "ip_allowlist": list(policy.ip_allowlist) if policy.ip_allowlist else [],
        "working_hours": policy.working_hours,
        "mfa_required": policy.mfa_required,
    }


@router.post("")
async def set_policy(
    membership_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    ip_allowlist: list[str] | None = None,
    working_hours: dict | None = None,
    mfa_required: bool = True,
):
    """Set access policy for a membership."""
    from uuid import uuid4

    from sqlalchemy import select

    from src.infrastructure.database.models.public.staff_access_policy import (
        StaffAccessPolicyModel,
    )

    result = await db.execute(
        select(StaffAccessPolicyModel).where(
            StaffAccessPolicyModel.membership_id == membership_id,
        )
    )
    policy = result.scalar_one_or_none()

    if policy:
        policy.ip_allowlist = ip_allowlist or []
        policy.working_hours = working_hours
        policy.mfa_required = mfa_required
    else:
        policy = StaffAccessPolicyModel(
            id=uuid4(),
            membership_id=membership_id,
            ip_allowlist=ip_allowlist or [],
            working_hours=working_hours,
            mfa_required=mfa_required,
        )
        db.add(policy)

    await db.commit()
    await db.refresh(policy)

    return {
        "id": str(policy.id),
        "status": "set",
    }


@router.delete("")
async def clear_policy(
    membership_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Clear access policy for a membership."""
    from sqlalchemy import delete

    from src.infrastructure.database.models.public.staff_access_policy import (
        StaffAccessPolicyModel,
    )

    await db.execute(
        delete(StaffAccessPolicyModel).where(
            StaffAccessPolicyModel.membership_id == membership_id,
        )
    )
    await db.commit()

    return {"status": "cleared"}

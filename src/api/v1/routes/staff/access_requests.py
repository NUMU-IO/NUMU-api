"""Access request routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.permissions import require_staff_edit
from src.api.dependencies.tenant import get_current_tenant
from src.infrastructure.database.models.public import TenantModel

router = APIRouter(prefix="/staff/access-requests", tags=["Staff - Access Requests"])


@router.get("")
async def list_access_requests(
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
):
    """List access requests for the tenant."""
    from sqlalchemy import select

    from src.infrastructure.database.models.public.access_request import (
        AccessRequestModel,
        AccessRequestStatus,
    )

    result = await db.execute(
        select(AccessRequestModel).where(
            AccessRequestModel.tenant_id == tenant.id,
            AccessRequestModel.status == AccessRequestStatus.PENDING,
        )
    )
    requests = result.scalars().all()

    return {
        "requests": [
            {
                "id": str(r.id),
                "requester_user_id": str(r.requester_user_id),
                "requested_role_ids": [str(rid) for rid in r.requested_role_ids],
                "requested_permissions": list(r.requested_permissions),
                "justification": r.justification,
                "status": r.status.value,
                "created_at": r.created_at.isoformat(),
            }
            for r in requests
        ]
    }


@router.post("/{request_id}/approve")
async def approve_access_request(
    request_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    review_reason: str | None = None,
):
    """Approve an access request."""
    from datetime import datetime

    from sqlalchemy import update

    from src.infrastructure.database.models.public.access_request import (
        AccessRequestModel,
        AccessRequestStatus,
    )

    result = await db.execute(
        update(AccessRequestModel)
        .where(
            AccessRequestModel.id == request_id,
            AccessRequestModel.status == AccessRequestStatus.PENDING,
        )
        .values(
            status=AccessRequestStatus.APPROVED,
            reviewer_user_id=user_id,
            reviewed_at=datetime.utcnow(),
            review_reason=review_reason,
        )
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found or already processed",
        )

    return {"status": "approved"}


@router.post("/{request_id}/deny")
async def deny_access_request(
    request_id: UUID,
    membership: Annotated[object, Depends(require_staff_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    review_reason: str | None = None,
):
    """Deny an access request."""
    from datetime import datetime

    from sqlalchemy import update

    from src.infrastructure.database.models.public.access_request import (
        AccessRequestModel,
        AccessRequestStatus,
    )

    result = await db.execute(
        update(AccessRequestModel)
        .where(
            AccessRequestModel.id == request_id,
            AccessRequestModel.status == AccessRequestStatus.PENDING,
        )
        .values(
            status=AccessRequestStatus.DENIED,
            reviewer_user_id=user_id,
            reviewed_at=datetime.utcnow(),
            review_reason=review_reason,
        )
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found or already processed",
        )

    return {"status": "denied"}


@router.post("/request")
async def create_access_request(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
    role_ids: list[UUID] | None = None,
    permissions: list[str] | None = None,
    justification: str | None = None,
):
    """Create an access request for elevated permissions."""
    from uuid import uuid4

    from src.infrastructure.database.models.public.access_request import (
        AccessRequestModel,
        AccessRequestStatus,
    )

    request = AccessRequestModel(
        id=uuid4(),
        tenant_id=tenant.id,
        requester_user_id=user_id,
        requested_role_ids=role_ids or [],
        requested_permissions=permissions or [],
        justification=justification,
        status=AccessRequestStatus.PENDING,
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)

    return {
        "id": str(request.id),
        "status": "pending",
    }

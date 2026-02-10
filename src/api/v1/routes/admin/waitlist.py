"""Admin waitlist management endpoints.

URL: /api/v1/admin/waitlist
Requires SUPER_ADMIN role.
"""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.api.v1.schemas.public.waitlist import (
    InviteWaitlistRequest,
    UpdatePriorityRequest,
    WaitlistEntryResponse,
)
from src.core.entities.waitlist import WaitlistStatus
from src.infrastructure.repositories.waitlist_repository import WaitlistRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_response(entry) -> WaitlistEntryResponse:
    return WaitlistEntryResponse(
        id=entry.id,
        email=entry.email,
        name=entry.name,
        company_name=entry.company_name,
        phone=entry.phone,
        status=entry.status,
        priority_score=entry.priority_score,
        referral_code=entry.referral_code,
        referral_count=entry.referral_count,
        invite_code=entry.invite_code,
        invited_at=entry.invited_at,
        converted_at=entry.converted_at,
        source=entry.source,
        notes=entry.notes,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[WaitlistEntryResponse]],
    summary="List waitlist entries",
)
async def list_waitlist(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[WaitlistStatus | None, Query(alias="status")] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """List all waitlist entries, ordered by priority then signup date."""
    repo = WaitlistRepository(db)

    skip = (page - 1) * page_size
    entries = await repo.list_all(status=status_filter, skip=skip, limit=page_size)
    total = await repo.count(status=status_filter)

    items = [_build_response(e) for e in entries]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
        ),
    )


@router.post(
    "/invite",
    response_model=SuccessResponse[WaitlistEntryResponse],
    summary="Send beta invite to waitlist entry",
)
async def invite_waitlist_entry(
    request: InviteWaitlistRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Send a beta invite to a waitlist entry.

    Generates a unique invite code, marks the entry as INVITED,
    and sends the beta invite email.
    """
    repo = WaitlistRepository(db)

    entry = await repo.get_by_id(request.entry_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waitlist entry not found",
        )

    if not entry.is_invitable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot invite entry with status: {entry.status}",
        )

    # Generate invite code
    invite_code = secrets.token_urlsafe(32)
    entry.invite(invite_code)
    updated = await repo.update(entry)
    await db.commit()

    # Send invite email (best-effort)
    try:
        from src.api.dependencies.services import get_email_service
        from src.core.interfaces.services.email_service import EmailMessage
        from src.infrastructure.external_services.resend.email_templates.beta_invite import (
            beta_invite_html,
        )

        email_service = get_email_service()
        await email_service.send_email(
            EmailMessage(
                to=updated.email,
                subject="You're invited to NUMU Beta!",
                html_content=beta_invite_html(
                    name=updated.name, invite_code=invite_code
                ),
            )
        )
    except Exception:
        logger.warning("beta_invite_email_failed", exc_info=True)

    logger.info(
        "waitlist_invited",
        extra={"entry_id": str(updated.id), "email": updated.email},
    )

    return SuccessResponse(
        data=_build_response(updated),
        message="Beta invite sent successfully",
    )


@router.patch(
    "/{entry_id}/priority",
    response_model=SuccessResponse[WaitlistEntryResponse],
    summary="Update waitlist entry priority",
)
async def update_priority(
    entry_id: Annotated[UUID, Path(description="Waitlist entry ID")],
    request: UpdatePriorityRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update priority score and optional notes for a waitlist entry."""
    repo = WaitlistRepository(db)

    entry = await repo.get_by_id(entry_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waitlist entry not found",
        )

    entry.priority_score = request.priority_score
    if request.notes is not None:
        entry.notes = request.notes
    entry.touch()

    updated = await repo.update(entry)
    await db.commit()

    return SuccessResponse(
        data=_build_response(updated),
        message="Priority updated",
    )

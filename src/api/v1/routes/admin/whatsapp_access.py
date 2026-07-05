"""Admin routes for the WhatsApp access-request queue (SUPER_ADMIN only).

Platform admins review merchant requests to enable the WhatsApp channel:
approve / reject a pending request, and disable / re-enable a store's access
at any time (kill-switch). The merchant-facing side (request + gate) lives in
``src/api/v1/routes/stores/whatsapp.py``.

The status FSM is the single source of truth on
``WhatsAppAccessRequestModel.status``:

    (none) --request--> PENDING --approve--> APPROVED --disable--> DISABLED
                           |                    ^                     |
                           +-----reject---> REJECTED <---------------+
                                                |     (enable ⇒ APPROVED)
                                                +----(enable)----> APPROVED

All actions are reversible (a disabled store can be re-enabled), so — matching
the reversible marketplace flag-management endpoints — these are gated by
``require_admin`` only, not the 2FA step-up reserved for irreversible actions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.public.whatsapp_access import (
    WhatsAppAccessRequestModel,
    WhatsAppAccessStatus,
)
from src.infrastructure.database.models.tenant.store import StoreModel

router = APIRouter(
    prefix="/whatsapp",
    tags=["Admin - WhatsApp Access"],
    dependencies=[Depends(require_admin)],
)

StatusFilter = Literal["pending", "approved", "rejected", "disabled", "all"]


# ── Schemas ──────────────────────────────────────────────────────────────────


class AdminWhatsAppAccessItem(BaseModel):
    """One row in the admin WhatsApp access queue, with store + requester joined
    in so the admin UI is readable without extra lookups."""

    id: UUID
    store_id: UUID
    store_name: str | None = None
    store_subdomain: str | None = None
    store_slug: str | None = None
    tenant_id: UUID
    status: str
    note: str | None = None
    contact_phone: str | None = None
    expected_volume: str | None = None
    requester_user_id: UUID
    requester_email: str | None = None
    reviewer_user_id: UUID | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminWhatsAppAccessListResponse(BaseModel):
    requests: list[AdminWhatsAppAccessItem]
    # Count per status across ALL rows (ignores the current filter) so the UI
    # can label its tabs, e.g. {"pending": 3, "approved": 12, ...}.
    counts: dict[str, int]


class AdminWhatsAppReviewBody(BaseModel):
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Reviewer note — the reason surfaced to the merchant.",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

# Which source states each target transition is allowed FROM. Keeping this table
# here makes the FSM auditable in one place; every endpoint routes through
# ``_transition`` so the rules can't drift per-verb.
_ALLOWED_SOURCES: dict[WhatsAppAccessStatus, set[WhatsAppAccessStatus]] = {
    WhatsAppAccessStatus.APPROVED: {
        WhatsAppAccessStatus.PENDING,
        WhatsAppAccessStatus.REJECTED,
        WhatsAppAccessStatus.DISABLED,
    },
    WhatsAppAccessStatus.REJECTED: {
        WhatsAppAccessStatus.PENDING,
        WhatsAppAccessStatus.APPROVED,
        WhatsAppAccessStatus.DISABLED,
    },
    WhatsAppAccessStatus.DISABLED: {
        WhatsAppAccessStatus.APPROVED,
        WhatsAppAccessStatus.PENDING,
    },
}


def _row_to_item(
    row: WhatsAppAccessRequestModel,
    store_name: str | None,
    store_subdomain: str | None,
    store_slug: str | None,
    requester_email: str | None,
) -> AdminWhatsAppAccessItem:
    return AdminWhatsAppAccessItem(
        id=row.id,
        store_id=row.store_id,
        store_name=store_name,
        store_subdomain=store_subdomain,
        store_slug=store_slug,
        tenant_id=row.tenant_id,
        status=row.status.value,
        note=row.note,
        contact_phone=row.contact_phone,
        expected_volume=row.expected_volume,
        requester_user_id=row.requester_user_id,
        requester_email=requester_email,
        reviewer_user_id=row.reviewer_user_id,
        reviewed_at=row.reviewed_at,
        review_reason=row.review_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _joined_select():
    """Base SELECT joining the store + requester for display columns."""
    return (
        select(
            WhatsAppAccessRequestModel,
            StoreModel.name,
            StoreModel.subdomain,
            StoreModel.slug,
            UserModel.email,
        )
        .join(StoreModel, StoreModel.id == WhatsAppAccessRequestModel.store_id)
        .outerjoin(
            UserModel, UserModel.id == WhatsAppAccessRequestModel.requester_user_id
        )
    )


async def _fetch_item(db: AsyncSession, request_id: UUID) -> AdminWhatsAppAccessItem:
    result = (
        await db.execute(
            _joined_select().where(WhatsAppAccessRequestModel.id == request_id)
        )
    ).one_or_none()
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    row, name, subdomain, slug, email = result
    return _row_to_item(row, name, subdomain, slug, email)


async def _transition(
    db: AsyncSession,
    request_id: UUID,
    target: WhatsAppAccessStatus,
    admin_id: UUID,
    reason: str | None,
) -> AdminWhatsAppAccessItem:
    """Move a request to ``target``, validating the source state against the FSM
    and stamping the reviewer. Idempotent: transitioning to the current state is
    a no-op that still refreshes the reviewer metadata."""
    row = (
        await db.execute(
            select(WhatsAppAccessRequestModel).where(
                WhatsAppAccessRequestModel.id == request_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )

    if row.status != target and row.status not in _ALLOWED_SOURCES[target]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot move WhatsApp access from '{row.status.value}' to"
                f" '{target.value}'."
            ),
        )

    row.status = target
    row.reviewer_user_id = admin_id
    row.reviewed_at = datetime.now(UTC)
    row.review_reason = reason
    await db.commit()
    return await _fetch_item(db, request_id)


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get(
    "/access-requests",
    response_model=SuccessResponse[AdminWhatsAppAccessListResponse],
    summary="List WhatsApp access requests",
)
async def list_access_requests(
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[StatusFilter, Query(alias="status")] = "pending",
) -> SuccessResponse[AdminWhatsAppAccessListResponse]:
    """List access requests, newest first. ``status`` filters the rows
    ('pending' by default; 'all' for everything). ``counts`` is always the
    full per-status breakdown so the UI can label tabs regardless of filter."""
    stmt = _joined_select().order_by(WhatsAppAccessRequestModel.created_at.desc())
    if status_filter != "all":
        stmt = stmt.where(
            WhatsAppAccessRequestModel.status == WhatsAppAccessStatus(status_filter)
        )

    rows = (await db.execute(stmt)).all()
    items = [_row_to_item(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    count_rows = (
        await db.execute(
            select(
                WhatsAppAccessRequestModel.status,
                func.count(),
            ).group_by(WhatsAppAccessRequestModel.status)
        )
    ).all()
    counts = {s.value: 0 for s in WhatsAppAccessStatus}
    for st, n in count_rows:
        counts[st.value] = n

    return SuccessResponse(
        data=AdminWhatsAppAccessListResponse(requests=items, counts=counts)
    )


@router.post(
    "/access-requests/{request_id}/approve",
    response_model=SuccessResponse[AdminWhatsAppAccessItem],
    summary="Approve a WhatsApp access request",
)
async def approve_access_request(
    request_id: UUID,
    body: AdminWhatsAppReviewBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[AdminWhatsAppAccessItem]:
    """Grant the store WhatsApp access. Valid from pending (the normal path) and
    also from rejected/disabled (acts as a re-enable)."""
    item = await _transition(
        db, request_id, WhatsAppAccessStatus.APPROVED, admin_id, body.notes
    )
    return SuccessResponse(data=item, message="WhatsApp access approved")


@router.post(
    "/access-requests/{request_id}/reject",
    response_model=SuccessResponse[AdminWhatsAppAccessItem],
    summary="Reject a WhatsApp access request",
)
async def reject_access_request(
    request_id: UUID,
    body: AdminWhatsAppReviewBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[AdminWhatsAppAccessItem]:
    """Decline the request. The reason in ``notes`` is shown to the merchant,
    who may then re-submit."""
    item = await _transition(
        db, request_id, WhatsAppAccessStatus.REJECTED, admin_id, body.notes
    )
    return SuccessResponse(data=item, message="WhatsApp access rejected")


@router.post(
    "/access-requests/{request_id}/disable",
    response_model=SuccessResponse[AdminWhatsAppAccessItem],
    summary="Disable (kill-switch) a store's WhatsApp access",
)
async def disable_access_request(
    request_id: UUID,
    body: AdminWhatsAppReviewBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[AdminWhatsAppAccessItem]:
    """Turn a store's WhatsApp access off without deleting the record. The store
    can no longer connect a number or toggle notifications until re-enabled."""
    item = await _transition(
        db, request_id, WhatsAppAccessStatus.DISABLED, admin_id, body.notes
    )
    return SuccessResponse(data=item, message="WhatsApp access disabled")


@router.post(
    "/access-requests/{request_id}/enable",
    response_model=SuccessResponse[AdminWhatsAppAccessItem],
    summary="Re-enable a disabled/rejected store's WhatsApp access",
)
async def enable_access_request(
    request_id: UUID,
    body: AdminWhatsAppReviewBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[AdminWhatsAppAccessItem]:
    """Restore access for a disabled or rejected store (→ approved)."""
    item = await _transition(
        db, request_id, WhatsAppAccessStatus.APPROVED, admin_id, body.notes
    )
    return SuccessResponse(data=item, message="WhatsApp access enabled")

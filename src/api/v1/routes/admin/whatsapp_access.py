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
from src.application.services.whatsapp_entitlement import (
    BillingUnavailableError,
    count_messages,
    open_payment,
    period_start,
)
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

StatusFilter = Literal[
    "pending", "awaiting_payment", "approved", "expired", "rejected", "disabled", "all"
]


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
    # Paid access — what the store was priced at and how long it runs.
    plan_key: str | None = None
    amount_cents: int | None = None
    currency: str | None = None
    billing_cycle: str | None = None
    active_until: datetime | None = None
    message_allowance: int | None = None
    #: Template messages sent in the current period — what the store is using.
    messages_used: int | None = None
    created_at: datetime
    updated_at: datetime


class AdminWhatsAppPriceBody(BaseModel):
    """What the store is asked to pay before the channel is switched on."""

    amount_cents: int = Field(gt=0, description="Price for one period, in piasters")
    billing_cycle: str = Field(
        default="monthly", pattern="^(monthly|quarterly|yearly)$"
    )
    #: Template messages included in the period. Omit for uncapped.
    message_allowance: int | None = Field(default=None, ge=0)
    plan_key: str = Field(default="whatsapp", max_length=20)
    notes: str | None = None


class AdminWhatsAppPriceResponse(BaseModel):
    request: AdminWhatsAppAccessItem
    #: What the merchant pays against — reference goes in the transfer note.
    intent_id: UUID
    special_reference: str
    amount_cents: int
    currency: str
    destination: str | None = None


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
        WhatsAppAccessStatus.AWAITING_PAYMENT,
        WhatsAppAccessStatus.REJECTED,
        WhatsAppAccessStatus.DISABLED,
        WhatsAppAccessStatus.EXPIRED,
    },
    WhatsAppAccessStatus.AWAITING_PAYMENT: {
        WhatsAppAccessStatus.PENDING,
        WhatsAppAccessStatus.APPROVED,
        WhatsAppAccessStatus.EXPIRED,
        WhatsAppAccessStatus.DISABLED,
    },
    WhatsAppAccessStatus.REJECTED: {
        WhatsAppAccessStatus.PENDING,
        WhatsAppAccessStatus.AWAITING_PAYMENT,
        WhatsAppAccessStatus.APPROVED,
        WhatsAppAccessStatus.DISABLED,
    },
    WhatsAppAccessStatus.DISABLED: {
        WhatsAppAccessStatus.APPROVED,
        WhatsAppAccessStatus.PENDING,
        WhatsAppAccessStatus.AWAITING_PAYMENT,
        WhatsAppAccessStatus.EXPIRED,
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
        plan_key=row.plan_key,
        amount_cents=row.amount_cents,
        currency=row.currency,
        billing_cycle=row.billing_cycle,
        active_until=row.active_until,
        message_allowance=row.message_allowance,
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
    # Approving by hand is a free grant. A lapsed paid period left on the row
    # would make that grant expire the instant it was given.
    if (
        target == WhatsAppAccessStatus.APPROVED
        and row.active_until is not None
        and row.active_until <= datetime.now(UTC)
    ):
        row.active_until = None
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
    now = datetime.now(UTC)
    for item, r in zip(items, rows, strict=True):
        if r[0].status == WhatsAppAccessStatus.APPROVED:
            item.messages_used = await count_messages(
                db, r[0].store_id, period_start(r[0], now)
            )

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
    "/access-requests/{request_id}/price",
    response_model=SuccessResponse[AdminWhatsAppPriceResponse],
    summary="Price a WhatsApp access request and bill the merchant",
)
async def price_access_request(
    request_id: UUID,
    body: AdminWhatsAppPriceBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[AdminWhatsAppPriceResponse]:
    """Set what this store pays for the channel and open a payment for it.

    The merchant transfers the amount with the returned reference in the note
    and uploads the receipt through the normal subscription-proof flow; OCR
    verification (or an admin approving the proof) switches access on for one
    period. Approving without pricing still works — that is an explicit free
    grant.

    A store inside a paid period keeps sending while the next bill is open:
    this is then a renewal, and cutting it off would punish paying early.
    """
    row = (
        await db.execute(
            select(WhatsAppAccessRequestModel).where(
                WhatsAppAccessRequestModel.id == request_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WhatsApp access request not found.",
        )

    now = datetime.now(UTC)
    row.plan_key = body.plan_key
    row.amount_cents = body.amount_cents
    row.currency = "EGP"
    row.billing_cycle = body.billing_cycle
    row.message_allowance = body.message_allowance
    try:
        intent = await open_payment(db, row, created_by_user_id=admin_id, now=now)
    except BillingUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="InstaPay is not configured, so nothing can be billed.",
        ) from exc

    paid_and_live = (
        row.status == WhatsAppAccessStatus.APPROVED
        and row.active_until is not None
        and row.active_until > now
    )
    if not paid_and_live:
        row.status = WhatsAppAccessStatus.AWAITING_PAYMENT
    row.reviewer_user_id = admin_id
    row.reviewed_at = now
    if body.notes:
        row.review_reason = body.notes
    await db.commit()

    item = await _fetch_item(db, request_id)
    return SuccessResponse(
        data=AdminWhatsAppPriceResponse(
            request=item,
            intent_id=intent.id,
            special_reference=intent.special_reference,
            amount_cents=intent.amount_cents,
            currency=intent.currency,
            destination=intent.display_destination,
        ),
        message="WhatsApp access priced — waiting on the merchant's payment",
    )


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

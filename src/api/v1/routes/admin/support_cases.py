"""Support cases — what staff owe a merchant, written down.

URL: /api/v1/admin/support-cases — requires SUPER_ADMIN.

Small on purpose. A case has a subject, a merchant it concerns, a priority,
an owner and a status; there is no threading, no SLA engine and no canned
replies, because those are a product decision and this is bookkeeping. The
point is that "17 open cases" on the overview stops being a number nobody
can click.

The status ladder is open → pending_merchant → resolved → closed, and the
transitions are one-way apart from reopening: a case that was resolved and
comes back is reopened rather than duplicated, so the history stays on one row.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter()

CaseStatus = Literal["open", "pending_merchant", "resolved", "closed"]
CasePriority = Literal["low", "normal", "high", "urgent"]
StatusFilter = Literal[
    "open", "pending_merchant", "resolved", "closed", "unresolved", "all"
]

#: What still counts as work. `resolved` is excluded: the merchant has their
#: answer and only the paperwork is left.
UNRESOLVED = ("open", "pending_merchant")


class SupportCase(BaseModel):
    id: str
    subject: str
    body: str | None
    status: CaseStatus
    priority: CasePriority
    category: str | None
    tenant_id: str | None
    store_id: str | None
    store_name: str | None
    entity_type: str | None
    entity_id: str | None
    reporter_email: str | None
    assignee_user_id: str | None
    assignee_email: str | None
    resolution: str | None
    first_response_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CaseListResponse(BaseModel):
    items: list[SupportCase]
    total: int
    #: Per-status counts across the whole table, for the tab labels.
    counts: dict[str, int]


class CreateCaseRequest(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    body: str | None = Field(default=None, max_length=8000)
    priority: CasePriority = "normal"
    category: str | None = Field(default=None, max_length=40)
    store_id: UUID | None = None
    tenant_id: UUID | None = None
    entity_type: str | None = Field(default=None, max_length=40)
    entity_id: str | None = Field(default=None, max_length=64)
    reporter_email: str | None = Field(default=None, max_length=255)


class UpdateCaseRequest(BaseModel):
    """Partial update. Absent means "leave alone"; the status ladder is
    enforced here rather than trusted from the client."""

    status: CaseStatus | None = None
    priority: CasePriority | None = None
    assignee_user_id: UUID | None = None
    #: Required by the endpoint when moving to resolved or closed.
    resolution: str | None = Field(default=None, max_length=4000)


_SELECT = """
    SELECT c.id, c.subject, c.body, c.status, c.priority, c.category,
           c.tenant_id, c.store_id, s.name AS store_name,
           c.entity_type, c.entity_id, c.reporter_email,
           c.assignee_user_id, u.email AS assignee_email,
           c.resolution, c.first_response_at, c.resolved_at, c.closed_at,
           c.created_at, c.updated_at
    FROM public.support_cases c
    LEFT JOIN public.stores s ON s.id = c.store_id
    LEFT JOIN public.users  u ON u.id = c.assignee_user_id
"""


def _to_model(r) -> SupportCase:
    return SupportCase(
        id=str(r["id"]),
        subject=r["subject"],
        body=r["body"],
        status=r["status"],
        priority=r["priority"],
        category=r["category"],
        tenant_id=str(r["tenant_id"]) if r["tenant_id"] else None,
        store_id=str(r["store_id"]) if r["store_id"] else None,
        store_name=r["store_name"],
        entity_type=r["entity_type"],
        entity_id=r["entity_id"],
        reporter_email=r["reporter_email"],
        assignee_user_id=str(r["assignee_user_id"]) if r["assignee_user_id"] else None,
        assignee_email=r["assignee_email"],
        resolution=r["resolution"],
        first_response_at=r["first_response_at"],
        resolved_at=r["resolved_at"],
        closed_at=r["closed_at"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.get(
    "",
    response_model=SuccessResponse[CaseListResponse],
    summary="List support cases",
    operation_id="admin_support_cases_list",
)
async def list_cases(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    case_status: Annotated[StatusFilter, Query(alias="status")] = "unresolved",
    priority: Annotated[CasePriority | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    where: list[str] = []
    params: dict[str, object] = {"limit": limit, "offset": offset}

    if case_status == "unresolved":
        where.append("c.status IN ('open', 'pending_merchant')")
    elif case_status != "all":
        where.append("c.status = :status")
        params["status"] = case_status
    if priority:
        where.append("c.priority = :priority")
        params["priority"] = priority
    if search:
        where.append("(c.subject ILIKE :q OR c.body ILIKE :q OR s.name ILIKE :q)")
        params["q"] = f"%{search}%"

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    rows = (
        (
            await db.execute(
                text(
                    f"""
                    {_SELECT}
                    {clause}
                    -- Urgent first, then oldest: priority decides what to pick
                    -- up, age decides which of the equally urgent ones.
                    ORDER BY CASE c.priority
                               WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                               WHEN 'normal' THEN 2 ELSE 3 END,
                             c.created_at ASC
                    LIMIT :limit OFFSET :offset
                    """  # nosec B608 - interpolates module literals only; values are bound
                ),
                params,
            )
        )
        .mappings()
        .all()
    )

    total = (
        await db.execute(
            text(
                f"""
                SELECT count(*) FROM public.support_cases c
                LEFT JOIN public.stores s ON s.id = c.store_id
                {clause}
                """  # nosec B608 - interpolates module literals only; values are bound
            ),
            params,
        )
    ).scalar() or 0

    count_rows = (
        await db.execute(
            text("SELECT status, count(*) FROM public.support_cases GROUP BY status")
        )
    ).all()
    counts = {"open": 0, "pending_merchant": 0, "resolved": 0, "closed": 0}
    for st, n in count_rows:
        if st in counts:
            counts[st] = n
    counts["unresolved"] = sum(counts[k] for k in UNRESOLVED)

    return SuccessResponse(
        data=CaseListResponse(
            items=[_to_model(r) for r in rows], total=total, counts=counts
        ),
        message="Support cases retrieved successfully",
    )


@router.post(
    "",
    response_model=SuccessResponse[SupportCase],
    status_code=http_status.HTTP_201_CREATED,
    summary="Open a support case",
    operation_id="admin_support_cases_create",
)
async def create_case(
    body: CreateCaseRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # If a store was given but no tenant, derive it — a case that knows the
    # store but not the merchant cannot be filtered by merchant later.
    tenant_id = body.tenant_id
    if tenant_id is None and body.store_id is not None:
        tenant_id = (
            await db.execute(
                text("SELECT tenant_id FROM public.stores WHERE id = :sid"),
                {"sid": body.store_id},
            )
        ).scalar()

    row = (
        (
            await db.execute(
                text(
                    """
                    INSERT INTO public.support_cases
                        (tenant_id, store_id, subject, body, priority, category,
                         entity_type, entity_id, reporter_email)
                    VALUES
                        (:tenant_id, :store_id, :subject, :body, :priority, :category,
                         :entity_type, :entity_id, :reporter_email)
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "store_id": body.store_id,
                    "subject": body.subject,
                    "body": body.body,
                    "priority": body.priority,
                    "category": body.category,
                    "entity_type": body.entity_type,
                    "entity_id": body.entity_id,
                    "reporter_email": body.reporter_email,
                },
            )
        )
        .mappings()
        .one()
    )
    await db.commit()

    created = (
        (await db.execute(text(f"{_SELECT} WHERE c.id = :id"), {"id": row["id"]}))
        .mappings()
        .one()
    )
    return SuccessResponse(data=_to_model(created), message="Case opened")


@router.patch(
    "/{case_id}",
    response_model=SuccessResponse[SupportCase],
    summary="Update a support case",
    operation_id="admin_support_cases_update",
)
async def update_case(
    case_id: UUID,
    body: UpdateCaseRequest,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    current = (
        (
            await db.execute(
                text(
                    "SELECT status, first_response_at FROM public.support_cases WHERE id = :id"
                ),
                {"id": case_id},
            )
        )
        .mappings()
        .first()
    )
    if current is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="No case with that id"
        )

    # Closing a case without saying how it ended leaves the next person with a
    # row that answers nothing, so the resolution is required at that step.
    if body.status in ("resolved", "closed"):
        resolution = body.resolution or ""
        if len(resolution.strip()) < 3:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A resolution is required to resolve or close a case",
            )

    now = datetime.now(UTC)
    sets = ["updated_at = :now"]
    params: dict[str, object] = {"now": now, "id": case_id}

    if body.status is not None:
        sets.append("status = :status")
        params["status"] = body.status
        if body.status == "resolved":
            sets.append("resolved_at = :now")
        elif body.status == "closed":
            sets.append("closed_at = :now")
        elif current["status"] in ("resolved", "closed"):
            # Reopened: clear the endings so the row does not read as both
            # open and resolved at once.
            sets.append("resolved_at = NULL")
            sets.append("closed_at = NULL")
    if body.priority is not None:
        sets.append("priority = :priority")
        params["priority"] = body.priority
    if body.assignee_user_id is not None:
        sets.append("assignee_user_id = :assignee")
        params["assignee"] = body.assignee_user_id
    if body.resolution is not None:
        sets.append("resolution = :resolution")
        params["resolution"] = body.resolution
    # The first time anyone touches a case counts as the first response.
    if current["first_response_at"] is None:
        sets.append("first_response_at = :now")

    await db.execute(
        # `sets` is built above from literal column assignments chosen by
        # branching on validated fields; every value is a bound parameter.
        text(  # nosec B608
            f"UPDATE public.support_cases SET {', '.join(sets)} WHERE id = :id"  # nosec B608
        ),
        params,
    )
    await db.commit()

    updated = (
        (await db.execute(text(f"{_SELECT} WHERE c.id = :id"), {"id": case_id}))
        .mappings()
        .one()
    )
    logger.info(
        "support_case_updated",
        extra={
            "case_id": str(case_id),
            "by": str(admin_id),
            "status": updated["status"],
        },
    )
    return SuccessResponse(data=_to_model(updated), message="Case updated")

"""Trust & risk — the COD review queue.

URL: /api/v1/admin/risk — requires SUPER_ADMIN.

Cash on delivery is the platform's exposure: the merchant ships, the courier
drives, and the shopper decides at the door. ``risk_assessments`` already
scores every order; this is the queue where a human agrees or disagrees with
that score.

The queue is NEWEST FIRST. It was oldest-first on the reasoning that the
longest-waiting orders are about to ship regardless — but with a real backlog
that put four-month-old assessments at the top of every page, so the order
that came in this morning, the one a decision can still change, was never on
screen. A stale COD assessment has already been resolved by the courier;
today's has not. Sort by `oldest` to get the original behaviour back.

Every decision requires a reason. The score is a judgement a human can
override, so the override has to say why — the reason is what a later
reviewer, or the merchant on the phone, actually reads.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter()

LevelFilter = Literal["all", "low", "medium", "high", "critical"]
StateFilter = Literal["open", "decided", "all"]
SortOrder = Literal["newest", "oldest"]

#: What a reviewer can decide. `accept` releases the order, `reject` holds it,
#: `escalate` keeps it open but marks it for a second pair of eyes.
Decision = Literal["accept", "reject", "escalate"]


class RiskItem(BaseModel):
    id: str
    order_id: str | None
    order_number: str | None
    store_id: str
    store_name: str | None
    tenant_id: str | None
    customer_name: str | None
    customer_email: str | None
    total_cents: int | None
    currency: str | None
    payment_method: str | None
    risk_score: int
    risk_level: str
    suggested_action: str | None
    #: Signal codes with weights — a bare score is not reviewable.
    factors: list[dict] | None
    action_taken: str | None
    action_taken_at: datetime | None
    action_note: str | None
    created_at: datetime


class RiskListResponse(BaseModel):
    items: list[RiskItem]
    total: int
    counts: dict[str, int]


class DecisionRequest(BaseModel):
    decision: Decision
    #: Required. "Invalid" is not a reason anyone can act on later.
    reason: str = Field(min_length=3, max_length=500)


@router.get(
    "",
    response_model=SuccessResponse[RiskListResponse],
    summary="List scored orders awaiting or past review",
    operation_id="admin_risk_list",
)
async def list_risk(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    level: Annotated[LevelFilter, Query()] = "all",
    state: Annotated[StateFilter, Query()] = "open",
    search: Annotated[str | None, Query(max_length=120)] = None,
    sort: Annotated[SortOrder, Query()] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    where = ["NOT (t.lifecycle_state = 'demo' OR t.is_internal IS TRUE)"]
    params: dict[str, object] = {"limit": limit, "offset": offset}

    if level != "all":
        where.append("r.risk_level = :level")
        params["level"] = level
    if state == "open":
        where.append("r.action_taken IS NULL")
    elif state == "decided":
        where.append("r.action_taken IS NOT NULL")
    if search:
        where.append(
            "(r.order_number ILIKE :q OR r.customer_name ILIKE :q"
            " OR r.customer_email ILIKE :q OR s.name ILIKE :q)"
        )
        params["q"] = f"%{search}%"

    clause = " AND ".join(where)
    # From a validated Literal, so only these two strings can ever reach SQL.
    direction = "ASC" if sort == "oldest" else "DESC"

    rows = (
        (
            await db.execute(
                text(
                    f"""
                SELECT r.id, r.order_id, r.order_number, r.store_id, s.name AS store_name,
                       r.tenant_id, r.customer_name, r.customer_email, r.total_cents,
                       r.currency, r.payment_method, r.risk_score, r.risk_level,
                       r.suggested_action, r.factors, r.action_taken, r.action_taken_at,
                       r.created_at,
                       r.decision_inputs ->> 'reason' AS action_note
                FROM public.risk_assessments r
                LEFT JOIN public.stores s ON s.id = r.store_id
                LEFT JOIN public.tenants t ON t.id = r.tenant_id
                WHERE {clause}
                -- Never by score: sorting that way looks decisive and starves
                -- everything below the top band. Direction is the caller's,
                -- and both values are module literals, not request text.
                ORDER BY r.created_at {direction}
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
                SELECT count(*)
                FROM public.risk_assessments r
                LEFT JOIN public.stores s ON s.id = r.store_id
                LEFT JOIN public.tenants t ON t.id = r.tenant_id
                WHERE {clause}
                """  # nosec B608 - interpolates module literals only; values are bound
            ),
            params,
        )
    ).scalar() or 0

    count_rows = (
        await db.execute(
            text(
                """
                SELECT r.risk_level, count(*)
                FROM public.risk_assessments r
                LEFT JOIN public.tenants t ON t.id = r.tenant_id
                WHERE r.action_taken IS NULL
                  AND NOT (t.lifecycle_state = 'demo' OR t.is_internal IS TRUE)
                GROUP BY r.risk_level
                """
            )
        )
    ).all()
    counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for lvl, n in count_rows:
        if lvl in counts:
            counts[lvl] = n
    counts["open"] = sum(counts[k] for k in ("low", "medium", "high", "critical"))

    return SuccessResponse(
        data=RiskListResponse(
            items=[
                RiskItem(
                    id=str(r["id"]),
                    order_id=str(r["order_id"]) if r["order_id"] else None,
                    order_number=r["order_number"],
                    store_id=str(r["store_id"]),
                    store_name=r["store_name"],
                    tenant_id=str(r["tenant_id"]) if r["tenant_id"] else None,
                    customer_name=r["customer_name"],
                    customer_email=r["customer_email"],
                    total_cents=r["total_cents"],
                    currency=r["currency"],
                    payment_method=r["payment_method"],
                    risk_score=r["risk_score"],
                    risk_level=r["risk_level"],
                    suggested_action=r["suggested_action"],
                    factors=r["factors"],
                    action_taken=r["action_taken"],
                    action_taken_at=r["action_taken_at"],
                    action_note=r["action_note"],
                    created_at=r["created_at"],
                )
                for r in rows
            ],
            total=total,
            counts=counts,
        ),
        message="Risk queue retrieved successfully",
    )


@router.post(
    "/{assessment_id}/decision",
    response_model=SuccessResponse[RiskItem],
    summary="Record a reviewer's decision on a scored order",
    operation_id="admin_risk_decide",
)
async def decide(
    assessment_id: UUID,
    body: DecisionRequest,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Agree or disagree with the score, on the record.

    The reason is stored alongside the decision rather than in a separate
    note, so a later reviewer sees the two together and cannot read one
    without the other.
    """
    row = (
        (
            await db.execute(
                text(
                    """
                SELECT r.id, r.action_taken
                FROM public.risk_assessments r
                WHERE r.id = :id
                """
                ),
                {"id": assessment_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No risk assessment with that id",
        )
    if row["action_taken"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Already decided: {row['action_taken']}",
        )

    await db.execute(
        text(
            """
            UPDATE public.risk_assessments
            SET action_taken = :decision,
                action_taken_at = :now,
                action_taken_by = :admin,
                decision_inputs = coalesce(decision_inputs, '{}'::jsonb)
                                  || jsonb_build_object('reason', :reason,
                                                        'decided_by', :admin::text),
                updated_at = :now
            WHERE id = :id
            """
        ),
        {
            "decision": body.decision,
            "now": datetime.now(UTC),
            "admin": str(admin_id),
            "reason": body.reason,
            "id": assessment_id,
        },
    )
    await db.commit()

    updated = (
        (
            await db.execute(
                text(
                    """
                SELECT r.id, r.order_id, r.order_number, r.store_id, s.name AS store_name,
                       r.tenant_id, r.customer_name, r.customer_email, r.total_cents,
                       r.currency, r.payment_method, r.risk_score, r.risk_level,
                       r.suggested_action, r.factors, r.action_taken, r.action_taken_at,
                       r.created_at, r.decision_inputs ->> 'reason' AS action_note
                FROM public.risk_assessments r
                LEFT JOIN public.stores s ON s.id = r.store_id
                WHERE r.id = :id
                """
                ),
                {"id": assessment_id},
            )
        )
        .mappings()
        .one()
    )

    logger.info(
        "risk_decision_recorded",
        extra={"assessment_id": str(assessment_id), "decision": body.decision},
    )

    return SuccessResponse(
        data=RiskItem(
            id=str(updated["id"]),
            order_id=str(updated["order_id"]) if updated["order_id"] else None,
            order_number=updated["order_number"],
            store_id=str(updated["store_id"]),
            store_name=updated["store_name"],
            tenant_id=str(updated["tenant_id"]) if updated["tenant_id"] else None,
            customer_name=updated["customer_name"],
            customer_email=updated["customer_email"],
            total_cents=updated["total_cents"],
            currency=updated["currency"],
            payment_method=updated["payment_method"],
            risk_score=updated["risk_score"],
            risk_level=updated["risk_level"],
            suggested_action=updated["suggested_action"],
            factors=updated["factors"],
            action_taken=updated["action_taken"],
            action_taken_at=updated["action_taken_at"],
            action_note=updated["action_note"],
            created_at=updated["created_at"],
        ),
        message="Decision recorded",
    )

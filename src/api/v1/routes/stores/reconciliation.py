"""Store payment reconciliation routes.

URL: /stores/{store_id}/reconciliation
Provides merchants with read-only visibility into daily reconciliation runs
and any mismatches that involve their orders.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.reconciliation import (
    PaymentReconciliationRunModel,
    ReconciliationMismatchModel,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ReconciliationRunSummary(BaseModel):
    id: str
    period_start: str
    period_end: str
    status: str
    total_orders_checked: int
    total_transactions_checked: int
    mismatches_found: int
    expected_amount_cents: int
    actual_amount_cents: int
    variance_cents: int  # expected - actual
    completed_at: str | None
    created_at: str


class MismatchSummary(BaseModel):
    id: str
    mismatch_type: str
    order_number: str | None
    gateway_transaction_id: str | None
    expected_amount_cents: int | None
    actual_amount_cents: int | None
    gateway: str | None
    notes: str | None
    resolved: bool
    created_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{store_id}/reconciliation/runs",
    response_model=SuccessResponse[list[ReconciliationRunSummary]],
    summary="List reconciliation runs",
    operation_id="store_list_reconciliation_runs",
)
async def list_reconciliation_runs(
    store_id: UUID,
    _user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=60),
):
    """List the most recent reconciliation runs (platform-wide, read-only for merchants)."""
    result = await db.execute(
        select(PaymentReconciliationRunModel)
        .order_by(desc(PaymentReconciliationRunModel.created_at))
        .offset(skip)
        .limit(limit)
    )
    runs = result.scalars().all()

    return SuccessResponse(
        data=[
            ReconciliationRunSummary(
                id=str(r.id),
                period_start=r.period_start.isoformat(),
                period_end=r.period_end.isoformat(),
                status=r.status,
                total_orders_checked=r.total_orders_checked,
                total_transactions_checked=r.total_transactions_checked,
                mismatches_found=r.mismatches_found,
                expected_amount_cents=r.expected_amount_cents,
                actual_amount_cents=r.actual_amount_cents,
                variance_cents=r.expected_amount_cents - r.actual_amount_cents,
                completed_at=r.completed_at.isoformat() if r.completed_at else None,
                created_at=r.created_at.isoformat(),
            )
            for r in runs
        ],
        message="Reconciliation runs retrieved",
    )


@router.get(
    "/{store_id}/reconciliation/runs/{run_id}/mismatches",
    response_model=SuccessResponse[list[MismatchSummary]],
    summary="Get mismatches for a reconciliation run",
    operation_id="store_list_run_mismatches",
)
async def list_run_mismatches(
    store_id: UUID,
    run_id: UUID,
    _user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    resolved: bool | None = Query(None),
):
    """Get mismatches for a specific run. Shows all mismatches (platform-wide view)."""
    q = select(ReconciliationMismatchModel).where(
        ReconciliationMismatchModel.run_id == run_id
    )
    if resolved is not None:
        q = q.where(ReconciliationMismatchModel.resolved == resolved)
    q = q.order_by(ReconciliationMismatchModel.created_at)

    result = await db.execute(q)
    mismatches = result.scalars().all()

    return SuccessResponse(
        data=[
            MismatchSummary(
                id=str(m.id),
                mismatch_type=m.mismatch_type,
                order_number=m.order_number,
                gateway_transaction_id=m.gateway_transaction_id,
                expected_amount_cents=m.expected_amount_cents,
                actual_amount_cents=m.actual_amount_cents,
                gateway=m.gateway,
                notes=m.notes,
                resolved=m.resolved,
                created_at=m.created_at.isoformat(),
            )
            for m in mismatches
        ],
        message="Mismatches retrieved",
    )

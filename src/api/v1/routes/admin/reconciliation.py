"""Admin payment reconciliation routes.

URL: /api/v1/admin/reconciliation
Requires SUPER_ADMIN role.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
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


class ReconciliationRunResponse(BaseModel):
    id: str
    gateway: str
    period_start: str
    period_end: str
    status: str
    total_orders_checked: int
    total_transactions_checked: int
    mismatches_found: int
    expected_amount_cents: int
    actual_amount_cents: int
    error_message: str | None
    completed_at: str | None
    created_at: str


class ReconciliationMismatchResponse(BaseModel):
    id: str
    run_id: str
    mismatch_type: str
    order_id: str | None
    order_number: str | None
    transaction_id: str | None
    gateway_transaction_id: str | None
    expected_amount_cents: int | None
    actual_amount_cents: int | None
    gateway: str | None
    notes: str | None
    resolved: bool
    resolved_at: str | None
    resolved_by: str | None
    created_at: str


class TriggerReconciliationRequest(BaseModel):
    target_date: date


class TriggerReconciliationResponse(BaseModel):
    run_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/runs",
    response_model=SuccessResponse[list[ReconciliationRunResponse]],
    summary="List reconciliation runs",
    operation_id="admin_list_reconciliation_runs",
)
async def list_reconciliation_runs(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
):
    """List reconciliation runs, newest first."""
    q = select(PaymentReconciliationRunModel).order_by(
        desc(PaymentReconciliationRunModel.created_at)
    )
    if status_filter:
        q = q.where(PaymentReconciliationRunModel.status == status_filter)
    q = q.offset(skip).limit(limit)

    result = await db.execute(q)
    runs = result.scalars().all()

    return SuccessResponse(
        data=[
            ReconciliationRunResponse(
                id=str(r.id),
                gateway=r.gateway,
                period_start=r.period_start.isoformat(),
                period_end=r.period_end.isoformat(),
                status=r.status,
                total_orders_checked=r.total_orders_checked,
                total_transactions_checked=r.total_transactions_checked,
                mismatches_found=r.mismatches_found,
                expected_amount_cents=r.expected_amount_cents,
                actual_amount_cents=r.actual_amount_cents,
                error_message=r.error_message,
                completed_at=r.completed_at.isoformat() if r.completed_at else None,
                created_at=r.created_at.isoformat(),
            )
            for r in runs
        ],
        message="Reconciliation runs retrieved",
    )


@router.get(
    "/runs/{run_id}/mismatches",
    response_model=SuccessResponse[list[ReconciliationMismatchResponse]],
    summary="List mismatches for a reconciliation run",
    operation_id="admin_list_reconciliation_mismatches",
)
async def list_run_mismatches(
    run_id: UUID,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    mismatch_type: str | None = Query(None),
    resolved: bool | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List all mismatches for a specific reconciliation run."""
    # Verify run exists
    run_result = await db.execute(
        select(PaymentReconciliationRunModel).where(
            PaymentReconciliationRunModel.id == run_id
        )
    )
    if not run_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        )

    q = select(ReconciliationMismatchModel).where(
        ReconciliationMismatchModel.run_id == run_id
    )
    if mismatch_type:
        q = q.where(ReconciliationMismatchModel.mismatch_type == mismatch_type)
    if resolved is not None:
        q = q.where(ReconciliationMismatchModel.resolved == resolved)
    q = q.order_by(ReconciliationMismatchModel.created_at).offset(skip).limit(limit)

    result = await db.execute(q)
    mismatches = result.scalars().all()

    return SuccessResponse(
        data=[
            ReconciliationMismatchResponse(
                id=str(m.id),
                run_id=str(m.run_id),
                mismatch_type=m.mismatch_type,
                order_id=str(m.order_id) if m.order_id else None,
                order_number=m.order_number,
                transaction_id=str(m.transaction_id) if m.transaction_id else None,
                gateway_transaction_id=m.gateway_transaction_id,
                expected_amount_cents=m.expected_amount_cents,
                actual_amount_cents=m.actual_amount_cents,
                gateway=m.gateway,
                notes=m.notes,
                resolved=m.resolved,
                resolved_at=m.resolved_at.isoformat() if m.resolved_at else None,
                resolved_by=m.resolved_by,
                created_at=m.created_at.isoformat(),
            )
            for m in mismatches
        ],
        message="Mismatches retrieved",
    )


@router.post(
    "/runs/trigger",
    response_model=SuccessResponse[TriggerReconciliationResponse],
    summary="Manually trigger reconciliation for a date",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="admin_trigger_reconciliation",
)
async def trigger_reconciliation(
    request: TriggerReconciliationRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Trigger a reconciliation run for the specified date (runs inline)."""
    from src.application.services.reconciliation_service import ReconciliationService

    svc = ReconciliationService(db)
    run = await svc.run_for_date(request.target_date)
    await db.commit()

    return SuccessResponse(
        data=TriggerReconciliationResponse(
            run_id=str(run.id),
            status=run.status,
            message=f"Reconciliation completed: {run.mismatches_found} mismatches found",
        ),
        message="Reconciliation triggered successfully",
    )

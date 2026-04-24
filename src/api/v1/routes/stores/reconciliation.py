"""Store payment reconciliation routes.

URL: /stores/{store_id}/reconciliation
Provides merchants with read-only visibility into daily reconciliation runs
and any mismatches that involve their orders.  Store owners can also trigger
a reconciliation run for a specific date.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.infrastructure.database.models.public.reconciliation import (
    PaymentReconciliationRunModel,
    ReconciliationMismatchModel,
)
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
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


class TriggerReconciliationRequest(BaseModel):
    target_date: date | None = None  # defaults to yesterday when omitted


class TriggerReconciliationResponse(BaseModel):
    run_id: str
    status: str
    message: str


class GatewayBreakdownRow(BaseModel):
    """One row per gateway in a date-window payment summary.

    Surfaces InstaPay volume alongside Paymob/Fawry/Kashier/Fawaterak/COD
    so merchants can reconcile each rail independently.
    """

    gateway: str
    status: str
    count: int
    total_cents: int
    currency: str


class GatewayBreakdownResponse(BaseModel):
    period_start: str
    period_end: str
    rows: list[GatewayBreakdownRow]
    total_successful_cents: int
    total_failed_cents: int


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
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=60),
):
    """List reconciliation runs scoped to this store."""
    result = await db.execute(
        select(PaymentReconciliationRunModel)
        .where(PaymentReconciliationRunModel.store_id == store.id)
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
    store: Annotated[Store, Depends(verify_store_ownership)],
    run_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    resolved: bool | None = Query(None),
):
    """Get mismatches for a specific reconciliation run scoped to this store."""
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


@router.post(
    "/{store_id}/reconciliation/runs/trigger",
    response_model=SuccessResponse[TriggerReconciliationResponse],
    summary="Trigger reconciliation for a date",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="store_trigger_reconciliation",
)
async def trigger_reconciliation(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: TriggerReconciliationRequest | None = None,
):
    """Trigger a reconciliation run for a given date (defaults to yesterday).

    Only the store owner may invoke this endpoint.
    """
    from src.application.services.reconciliation_service import ReconciliationService

    target_date = (
        request.target_date
        if request and request.target_date
        else date.today() - timedelta(days=1)
    )

    svc = ReconciliationService(db)
    run = await svc.run_for_date(target_date, store_id=store.id)
    await db.commit()

    return SuccessResponse(
        data=TriggerReconciliationResponse(
            run_id=str(run.id),
            status=run.status,
            message=f"Reconciliation completed: {run.mismatches_found} mismatches found",
        ),
        message="Reconciliation triggered successfully",
    )


@router.get(
    "/{store_id}/reconciliation/gateway-breakdown",
    response_model=SuccessResponse[GatewayBreakdownResponse],
    summary="Payment volume broken down by gateway",
    operation_id="store_reconciliation_gateway_breakdown",
)
async def gateway_breakdown(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_from: date | None = Query(
        None, description="Inclusive start date (UTC). Defaults to 7 days ago."
    ),
    date_to: date | None = Query(
        None, description="Inclusive end date (UTC). Defaults to today."
    ),
) -> SuccessResponse[GatewayBreakdownResponse]:
    """Aggregate ``payment_transactions`` by gateway for a date range.

    Returns one row per (gateway, status, currency) triple — so a
    merchant can see at a glance how much arrived via InstaPay vs.
    Paymob vs. COD, and how many of each failed. Read-only; does not
    trigger a reconciliation run.
    """
    today = datetime.now(UTC).date()
    start_date = date_from or (today - timedelta(days=7))
    end_date = date_to or today
    period_start = datetime.combine(start_date, time.min, tzinfo=UTC)
    # end_date is inclusive — use the start of the day *after* to catch
    # everything timestamped on end_date itself.
    period_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)

    result = await db.execute(
        select(
            PaymentTransactionModel.gateway,
            PaymentTransactionModel.status,
            PaymentTransactionModel.currency,
            func.count(PaymentTransactionModel.id),
            func.coalesce(func.sum(PaymentTransactionModel.amount_cents), 0),
        )
        .where(
            PaymentTransactionModel.store_id == store.id,
            PaymentTransactionModel.created_at >= period_start,
            PaymentTransactionModel.created_at < period_end,
        )
        .group_by(
            PaymentTransactionModel.gateway,
            PaymentTransactionModel.status,
            PaymentTransactionModel.currency,
        )
        .order_by(
            PaymentTransactionModel.gateway,
            PaymentTransactionModel.status,
        )
    )

    rows: list[GatewayBreakdownRow] = []
    success_total = 0
    failed_total = 0
    for gateway, row_status, currency, count, total in result.all():
        total_cents = int(total)
        rows.append(
            GatewayBreakdownRow(
                gateway=gateway,
                status=row_status,
                count=int(count),
                total_cents=total_cents,
                currency=currency,
            )
        )
        if row_status == "success":
            success_total += total_cents
        elif row_status == "failed":
            failed_total += total_cents

    return SuccessResponse(
        data=GatewayBreakdownResponse(
            period_start=period_start.date().isoformat(),
            period_end=end_date.isoformat(),
            rows=rows,
            total_successful_cents=success_total,
            total_failed_cents=failed_total,
        ),
        message="Gateway breakdown retrieved",
    )

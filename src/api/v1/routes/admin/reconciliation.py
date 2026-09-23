"""Admin payment reconciliation routes.

URL: /api/v1/admin/reconciliation
Requires SUPER_ADMIN role.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import String, and_, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin, require_admin_2fa
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.application.services.audit_service import AuditService
from src.core.events.order_events import OrderStatusChangedEvent
from src.core.logging import get_logger
from src.infrastructure.database.models.public.reconciliation import (
    PaymentReconciliationRunModel,
    ReconciliationMismatchModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.events.setup import get_event_bus
from src.infrastructure.repositories.order_repository import OrderRepository

logger = get_logger(__name__)

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


# ---------------------------------------------------------------------------
# Live transaction / order view
# ---------------------------------------------------------------------------

# Gateway statuses that mean the money was taken (same set as
# ReconciliationService).
_SUCCESS_TX_STATUSES = ("success", "paid", "completed", "captured")


class TransactionOrderRow(BaseModel):
    transaction_id: str
    created_at: str
    gateway: str
    channel: str
    tx_status: str
    amount_cents: int
    currency: str
    gateway_transaction_id: str | None
    store_id: str
    store_name: str | None
    order_id: str | None
    order_number: str | None
    order_status: str | None
    payment_status: str | None
    order_total_cents: int | None
    # "paid_not_recorded": the gateway took the money, the order is not paid.
    # "order_missing": the transaction points at no order.
    mismatch: str | None


def _row(tx, order, store_name: str | None) -> TransactionOrderRow:
    mismatch = None
    if order is None:
        mismatch = "order_missing"
    elif tx.status in _SUCCESS_TX_STATUSES and order.payment_status != "paid":
        mismatch = "paid_not_recorded"
    return TransactionOrderRow(
        transaction_id=str(tx.id),
        created_at=tx.created_at.isoformat(),
        gateway=tx.gateway,
        channel=tx.channel,
        tx_status=tx.status,
        amount_cents=tx.amount_cents,
        currency=tx.currency,
        gateway_transaction_id=tx.gateway_transaction_id,
        store_id=str(tx.store_id),
        store_name=store_name,
        order_id=str(order.id) if order else None,
        order_number=order.order_number if order else None,
        order_status=str(order.status) if order else None,
        payment_status=str(order.payment_status) if order else None,
        order_total_cents=order.total if order else None,
        mismatch=mismatch,
    )


@router.get(
    "/transactions",
    response_model=SuccessResponse[PaginatedListResponse[TransactionOrderRow]],
    summary="Gateway transactions next to their orders",
    operation_id="admin_list_reconciliation_transactions",
)
async def list_transactions(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    gateway: str | None = Query(None),
    store_id: UUID | None = Query(None),
    mismatch_only: bool = Query(False),
    days: int = Query(30, ge=1, le=365),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Every payment transaction in the window with its order's state.

    Unlike the batch runs this reads live rows, so a paid transaction whose
    order never flipped to paid shows up the moment it happens.
    """
    paid_not_recorded = and_(
        PaymentTransactionModel.status.in_(_SUCCESS_TX_STATUSES),
        OrderModel.id.isnot(None),
        func.lower(cast(OrderModel.payment_status, String)) != "paid",
    )
    order_missing = OrderModel.id.is_(None)

    conditions = [
        PaymentTransactionModel.created_at >= datetime.now(UTC) - timedelta(days=days)
    ]
    if gateway:
        conditions.append(PaymentTransactionModel.gateway == gateway)
    if store_id:
        conditions.append(PaymentTransactionModel.store_id == store_id)
    if mismatch_only:
        conditions.append(or_(paid_not_recorded, order_missing))

    base = (
        select(PaymentTransactionModel, OrderModel, StoreModel.name)
        .outerjoin(OrderModel, OrderModel.id == PaymentTransactionModel.order_id)
        .outerjoin(StoreModel, StoreModel.id == PaymentTransactionModel.store_id)
        .where(*conditions)
    )
    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0
    rows = (
        await db.execute(
            base.order_by(desc(PaymentTransactionModel.created_at))
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()

    return SuccessResponse(
        data=PaginatedListResponse(
            items=[_row(tx, order, name) for tx, order, name in rows],
            total=total,
            page=page,
            page_size=limit,
            total_pages=(total + limit - 1) // limit,
        ),
        message="Transactions retrieved",
    )


@router.post(
    "/transactions/{transaction_id}/mark-paid",
    response_model=SuccessResponse[TransactionOrderRow],
    summary="Mark the order of a successful transaction paid",
    operation_id="admin_reconcile_mark_paid",
    dependencies=[Depends(require_admin_2fa(max_age_seconds=300))],
)
async def mark_transaction_order_paid(
    transaction_id: Annotated[UUID, Path()],
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Apply a successful gateway payment the order never recorded.

    Goes through ``Order.mark_as_paid`` and publishes the status event a
    gateway webhook sends, so shipment booking, notifications and the release
    of a held AWAITING_PAYMENT order all run as if the webhook had worked.
    """
    tx = await db.get(PaymentTransactionModel, transaction_id)
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    if tx.status not in _SUCCESS_TX_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Transaction status is {tx.status}; only successful payments apply.",
        )
    if tx.order_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Transaction has no order")

    order_repo = OrderRepository(db)
    order = await order_repo.get_by_id(tx.order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.is_paid:
        raise HTTPException(status.HTTP_409_CONFLICT, "Order is already paid")

    old = {"status": str(order.status), "payment_status": str(order.payment_status)}
    order.mark_as_paid(
        payment_id=tx.gateway_transaction_id or str(tx.id),
        payment_method=tx.gateway,
    )
    await order_repo.update(order)

    await AuditService(db).log(
        event_type="admin.reconcile_mark_paid",
        action="update",
        resource_type="order",
        resource_id=str(order.id),
        user_id=admin_id,
        store_id=order.store_id,
        tenant_id=order.tenant_id,
        old_value=old,
        new_value={
            "status": str(order.status),
            "payment_status": str(order.payment_status),
        },
        details={
            "transaction_id": str(tx.id),
            "gateway": tx.gateway,
            "gateway_transaction_id": tx.gateway_transaction_id,
        },
    )

    store = await db.get(StoreModel, order.store_id)
    get_event_bus().publish(
        OrderStatusChangedEvent(
            order_id=order.id,
            order_number=order.order_number,
            store_id=order.store_id,
            store_name=store.name if store else "",
            customer_id=order.customer_id,
            customer_name=order.shipping_address.full_name
            if order.shipping_address
            else None,
            previous_status=old["status"],
            new_status=str(order.status),
        )
    )
    logger.info(
        "admin_reconcile_mark_paid",
        order_id=str(order.id),
        transaction_id=str(tx.id),
        admin_id=str(admin_id),
    )

    return SuccessResponse(
        data=_row(tx, order, store.name if store else None),
        message="Order marked paid",
    )

"""Admin order management endpoints.

URL: /api/v1/admin/orders
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.core.entities.order import VALID_STATUS_TRANSITIONS, OrderStatus, PaymentStatus
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AdminOrderListItem(BaseModel):
    id: str
    store_id: str
    store_name: str | None = None
    customer_id: str
    customer_name: str | None = None
    customer_email: str | None = None
    order_number: str
    status: str
    payment_status: str
    fulfillment_status: str
    total: int
    currency: str
    item_count: int
    payment_method: str | None = None
    created_at: str
    updated_at: str


class AdminOrderDetail(BaseModel):
    id: str
    store_id: str
    store_name: str | None = None
    tenant_id: str | None = None
    customer_id: str
    customer_name: str | None = None
    customer_email: str | None = None
    order_number: str
    status: str
    payment_status: str
    fulfillment_status: str
    line_items: list[dict]
    shipping_address: dict
    billing_address: dict | None = None
    subtotal: int
    shipping_cost: int
    tax_amount: int
    discount_amount: int
    total: int
    currency: str
    coupon_code: str | None = None
    payment_method: str | None = None
    payment_id: str | None = None
    shipping_method: str | None = None
    tracking_number: str | None = None
    notes: str | None = None
    customer_notes: str | None = None
    extra_data: dict | None = None
    cancelled_at: str | None = None
    paid_at: str | None = None
    fulfilled_at: str | None = None
    created_at: str
    updated_at: str


class UpdateOrderStatusRequest(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(dt) -> str | None:
    return dt.isoformat() if dt else None


def _order_to_list_item(o: OrderModel) -> AdminOrderListItem:
    customer = o.customer
    store = o.store
    items = o.line_items or []
    item_count = sum(i.get("quantity", 1) for i in items) if items else 0

    return AdminOrderListItem(
        id=str(o.id),
        store_id=str(o.store_id),
        store_name=store.name if store else None,
        customer_id=str(o.customer_id),
        customer_name=f"{customer.first_name} {customer.last_name}"
        if customer
        else None,
        customer_email=customer.email if customer else None,
        order_number=o.order_number,
        status=o.status.value if hasattr(o.status, "value") else str(o.status),
        payment_status=o.payment_status.value
        if hasattr(o.payment_status, "value")
        else str(o.payment_status),
        fulfillment_status=o.fulfillment_status.value
        if hasattr(o.fulfillment_status, "value")
        else str(o.fulfillment_status),
        total=o.total,
        currency=o.currency,
        item_count=item_count,
        payment_method=o.payment_method,
        created_at=_ts(o.created_at) or "",
        updated_at=_ts(o.updated_at) or "",
    )


def _order_to_detail(o: OrderModel) -> AdminOrderDetail:
    customer = o.customer
    store = o.store

    return AdminOrderDetail(
        id=str(o.id),
        store_id=str(o.store_id),
        store_name=store.name if store else None,
        tenant_id=str(o.tenant_id) if o.tenant_id else None,
        customer_id=str(o.customer_id),
        customer_name=f"{customer.first_name} {customer.last_name}"
        if customer
        else None,
        customer_email=customer.email if customer else None,
        order_number=o.order_number,
        status=o.status.value if hasattr(o.status, "value") else str(o.status),
        payment_status=o.payment_status.value
        if hasattr(o.payment_status, "value")
        else str(o.payment_status),
        fulfillment_status=o.fulfillment_status.value
        if hasattr(o.fulfillment_status, "value")
        else str(o.fulfillment_status),
        line_items=o.line_items or [],
        shipping_address=o.shipping_address or {},
        billing_address=o.billing_address,
        subtotal=o.subtotal,
        shipping_cost=o.shipping_cost,
        tax_amount=o.tax_amount,
        discount_amount=o.discount_amount,
        total=o.total,
        currency=o.currency,
        coupon_code=o.coupon_code,
        payment_method=o.payment_method,
        payment_id=o.payment_id,
        shipping_method=o.shipping_method,
        tracking_number=o.tracking_number,
        notes=o.notes,
        customer_notes=o.customer_notes,
        extra_data=o.extra_data,
        cancelled_at=_ts(o.cancelled_at),
        paid_at=_ts(o.paid_at),
        fulfilled_at=_ts(o.fulfilled_at),
        created_at=_ts(o.created_at) or "",
        updated_at=_ts(o.updated_at) or "",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[AdminOrderListItem]],
    summary="List all orders (admin)",
    operation_id="admin_list_orders",
)
async def list_orders(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    order_status: Annotated[str | None, Query(alias="status")] = None,
    payment_status: Annotated[str | None, Query()] = None,
    store_id: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List all orders across all stores (paginated).

    Demo and internal tenants are excluded.
    """
    excluded_tenant_ids = (
        select(TenantModel.id)
        .where(
            (TenantModel.lifecycle_state == TenantLifecycleState.DEMO.value)
            | (TenantModel.is_internal.is_(True))
        )
        .scalar_subquery()
    )
    not_excluded = OrderModel.tenant_id.notin_(excluded_tenant_ids)

    query = select(OrderModel).options(
        selectinload(OrderModel.customer),
        selectinload(OrderModel.store),
    ).where(not_excluded)
    count_query = select(func.count(OrderModel.id)).where(not_excluded)

    # Filters
    if order_status:
        try:
            parsed = OrderStatus(order_status)
            query = query.where(OrderModel.status == parsed)
            count_query = count_query.where(OrderModel.status == parsed)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid order status filter: {order_status}",
            )

    if payment_status:
        try:
            parsed = PaymentStatus(payment_status)
            query = query.where(OrderModel.payment_status == parsed)
            count_query = count_query.where(OrderModel.payment_status == parsed)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid payment status filter: {payment_status}",
            )

    if store_id:
        query = query.where(OrderModel.store_id == store_id)
        count_query = count_query.where(OrderModel.store_id == store_id)

    if search:
        term = f"%{search}%"
        search_filter = or_(
            OrderModel.order_number.ilike(term),
            OrderModel.customer.has(CustomerModel.email.ilike(term)),
            OrderModel.customer.has(CustomerModel.first_name.ilike(term)),
            OrderModel.customer.has(CustomerModel.last_name.ilike(term)),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Pagination
    skip = (page - 1) * limit
    query = query.order_by(OrderModel.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    orders = result.scalars().unique().all()

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    items = [_order_to_list_item(o) for o in orders]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=items,
            total=total,
            page=page,
            page_size=limit,
            total_pages=(total + limit - 1) // limit if limit > 0 else 0,
        ),
        message="Orders retrieved successfully",
    )


@router.get(
    "/{order_id}",
    response_model=SuccessResponse[AdminOrderDetail],
    summary="Get order details (admin)",
    operation_id="admin_get_order",
)
async def get_order(
    order_id: Annotated[UUID, Path(description="Order ID")],
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a single order with full details."""
    query = (
        select(OrderModel)
        .options(
            selectinload(OrderModel.customer),
            selectinload(OrderModel.store),
        )
        .where(OrderModel.id == order_id)
    )
    result = await db.execute(query)
    order = result.scalars().first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    return SuccessResponse(
        data=_order_to_detail(order),
        message="Order retrieved successfully",
    )


@router.patch(
    "/{order_id}/status",
    response_model=SuccessResponse[AdminOrderDetail],
    summary="Update order status (admin)",
    operation_id="admin_update_order_status",
)
async def update_order_status(
    order_id: Annotated[UUID, Path(description="Order ID")],
    request: UpdateOrderStatusRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update an order's status (admin override)."""
    query = (
        select(OrderModel)
        .options(
            selectinload(OrderModel.customer),
            selectinload(OrderModel.store),
        )
        .where(OrderModel.id == order_id)
    )
    result = await db.execute(query)
    order = result.scalars().first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    # Parse new status
    try:
        new_status = OrderStatus(request.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {request.status}",
        )

    # Validate transition
    current = order.status
    valid_transitions = VALID_STATUS_TRANSITIONS.get(current, [])
    if new_status not in valid_transitions:
        valid_names = [s.value for s in valid_transitions]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid transition: {current.value} -> {new_status.value}. "
            f"Valid: {valid_names or 'none (terminal state)'}",
        )

    old_status = current.value
    order.status = new_status
    await db.commit()
    await db.refresh(order)

    # Publish domain event for notifications / activity log
    try:
        from src.core.events.order_events import OrderStatusChangedEvent
        from src.infrastructure.events.setup import get_event_bus

        customer = order.customer
        store = order.store
        event = OrderStatusChangedEvent(
            order_id=order.id,
            order_number=order.order_number,
            store_id=order.store_id,
            store_name=store.name if store else "Unknown",
            customer_id=order.customer_id,
            customer_email=customer.email if customer else None,
            customer_name=(
                f"{customer.first_name} {customer.last_name}" if customer else None
            ),
            previous_status=old_status,
            new_status=new_status.value,
            tracking_number=order.tracking_number,
            carrier=order.shipping_method,
            language=(store.default_language if store else "en") or "en",
        )
        get_event_bus().publish(event)
    except Exception:
        logger.exception("admin_order_event_publish_failed")

    return SuccessResponse(
        data=_order_to_detail(order),
        message=f"Order status updated to {new_status.value}",
    )


@router.delete(
    "/{order_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete an order (admin)",
    operation_id="admin_delete_order",
)
async def delete_order(
    order_id: Annotated[UUID, Path(description="Order ID")],
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Permanently delete an order and all its related records.

    Cascades via FK constraints: activities, instapay intents, payment
    proofs, refunds, shipments, and returns are deleted. Invoices and
    abandoned checkouts have their order_id set to NULL.
    """
    result = await db.execute(
        select(OrderModel).where(OrderModel.id == order_id)
    )
    order = result.scalars().first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    logger.warning(
        "admin_hard_delete_order order_id=%s order_number=%s admin_id=%s",
        order.id,
        order.order_number,
        admin_id,
    )

    await db.execute(delete(OrderModel).where(OrderModel.id == order_id))
    await db.commit()

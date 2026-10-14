"""Admin order management endpoints.

URL: /api/v1/admin/orders
Requires SUPER_ADMIN role.
"""

import logging
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.sanitization import SanitizedStr
from src.api.responses import SuccessResponse
from src.api.v1.routes.stores import orders as store_orders
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.api.v1.schemas.tenant.order import (
    CreateOrderCommentRequest,
    OrderActivitiesResponse,
    OrderActivityResponse,
)
from src.application.services.audit_service import AuditService
from src.application.services.stock_service import try_restock_order
from src.core.entities.instapay import ManualPaymentIntentStatus
from src.core.entities.order import VALID_STATUS_TRANSITIONS, OrderStatus, PaymentStatus
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.repositories.customer_repository import CustomerRepository
from src.infrastructure.repositories.instapay_intent_repository import (
    ManualPaymentIntentRepository,
)
from src.infrastructure.repositories.order_activity_repository import (
    OrderActivityRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.payment_proof_repository import (
    PaymentProofRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AdminOrderListItem(BaseModel):
    id: str
    store_id: str
    store_name: str | None = None
    tenant_id: str | None = None
    customer_id: str
    customer_name: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
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
    customer_phone: str | None = None
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
    #: Newest risk assessment for this order, if any.
    risk_score: int | None = None
    risk_level: str | None = None
    customer_history: "CustomerHistory | None" = None


class CustomerHistoryOrder(BaseModel):
    id: str
    order_number: str
    status: str
    payment_status: str
    total: int
    currency: str
    created_at: str


class CustomerHistory(BaseModel):
    """The same customer's orders at this store, this one included."""

    orders: int
    delivered: int
    cancelled: int
    returned: int
    paid_total: int
    first_order_at: str | None
    recent: list[CustomerHistoryOrder]


AdminOrderDetail.model_rebuild()


class UpdateOrderStatusRequest(BaseModel):
    status: str


class AdminOrderNoteRequest(BaseModel):
    body: SanitizedStr = Field(..., min_length=1, max_length=2000)


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
        tenant_id=str(o.tenant_id) if o.tenant_id else None,
        customer_id=str(o.customer_id),
        customer_name=f"{customer.first_name} {customer.last_name}"
        if customer
        else None,
        customer_email=customer.email if customer else None,
        customer_phone=customer.phone if customer else None,
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
        customer_phone=customer.phone if customer else None,
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


async def _load_order(db: AsyncSession, order_id: UUID) -> OrderModel:
    order = (
        await db.execute(
            select(OrderModel)
            .options(
                selectinload(OrderModel.customer),
                selectinload(OrderModel.store),
            )
            .where(OrderModel.id == order_id)
        )
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    return order


async def _load_entity(db: AsyncSession, order_id: UUID):
    entity = await OrderRepository(db).get_by_id(order_id)
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    return entity


async def _close_live_intent(db: AsyncSession, order_id: UUID) -> None:
    """The money is in: a live manual-payment intent left to expire would
    make the InstaPay sweep cancel and restock this order."""
    repo = ManualPaymentIntentRepository(db)
    intent = await repo.get_by_order_id(order_id)
    if intent is not None and intent.status in (
        ManualPaymentIntentStatus.AWAITING_PAYMENT,
        ManualPaymentIntentStatus.PROOF_RECEIVED,
    ):
        await repo.update_status(intent.id, ManualPaymentIntentStatus.PAID)


async def _audit(db: AsyncSession, admin_id: UUID, entity, event: str, **details):
    await AuditService(db).log(
        event_type=f"order.{event}_by_staff",
        action="update",
        resource_type="order",
        resource_id=str(entity.id),
        user_id=admin_id,
        tenant_id=entity.tenant_id,
        store_id=entity.store_id,
        details={
            "order_number": entity.order_number,
            "actor_type": "staff",
            **details,
        },
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

    query = (
        select(OrderModel)
        .options(
            selectinload(OrderModel.customer),
            selectinload(OrderModel.store),
        )
        .where(not_excluded)
    )
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
    order = await _load_order(db, order_id)
    detail = _order_to_detail(order)
    risk = (
        await db.execute(
            text(
                "SELECT risk_score, risk_level FROM public.risk_assessments "
                "WHERE order_id = :id ORDER BY created_at DESC LIMIT 1"
            ),
            {"id": order.id},
        )
    ).first()
    if risk is not None:
        detail.risk_score, detail.risk_level = risk.risk_score, risk.risk_level
    if order.customer_id:
        detail.customer_history = await _customer_history(db, order)

    return SuccessResponse(data=detail, message="Order retrieved successfully")


def _enum_value(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


async def _customer_history(db: AsyncSession, order: OrderModel) -> CustomerHistory:
    """What support needs to judge a COD order: has this customer ordered
    here before, and did those orders arrive or come back."""
    same_customer = OrderModel.customer_id == order.customer_id
    counts = (
        await db.execute(
            select(
                func.count(OrderModel.id),
                func.count().filter(OrderModel.status == OrderStatus.DELIVERED),
                func.count().filter(OrderModel.status == OrderStatus.CANCELLED),
                func.count().filter(OrderModel.status == OrderStatus.RETURNED),
                func.coalesce(
                    func.sum(OrderModel.total).filter(
                        OrderModel.payment_status == PaymentStatus.PAID
                    ),
                    0,
                ),
                func.min(OrderModel.created_at),
            ).where(same_customer)
        )
    ).one()
    recent = (
        await db.scalars(
            select(OrderModel)
            .where(same_customer)
            .order_by(OrderModel.created_at.desc())
            .limit(10)
        )
    ).all()
    return CustomerHistory(
        orders=counts[0],
        delivered=counts[1],
        cancelled=counts[2],
        returned=counts[3],
        paid_total=int(counts[4]),
        first_order_at=_ts(counts[5]),
        recent=[
            CustomerHistoryOrder(
                id=str(o.id),
                order_number=o.order_number,
                status=_enum_value(o.status),
                payment_status=_enum_value(o.payment_status),
                total=o.total,
                currency=o.currency,
                created_at=_ts(o.created_at) or "",
            )
            for o in recent
        ],
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
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update an order's status (admin override).

    Cancelled and returned give the debited stock back, once."""
    order = await _load_order(db, order_id)

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
    restocked = False
    if new_status in (OrderStatus.CANCELLED, OrderStatus.RETURNED):
        entity = await _load_entity(db, order_id)
        restocked = await try_restock_order(
            db, entity, reason=f"admin_{new_status.value}"
        )
        if restocked:
            order.extra_data = entity.metadata
    order.status = new_status
    await _audit(
        db,
        admin_id,
        order,
        "status_changed",
        from_status=old_status,
        to_status=new_status.value,
        restocked=restocked,
    )
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
    result = await db.execute(select(OrderModel).where(OrderModel.id == order_id))
    order = result.scalars().first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    if (
        order.payment_status not in (PaymentStatus.PENDING, PaymentStatus.FAILED)
        or order.deposit_paid_at is not None
        or await PaymentProofRepository(db).has_recorded_payments(order.id)
    ):
        # Deleting would leave the money, the invoice and the commission
        # behind with no order. Un-mark or refund it first.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Money was taken against this order. Refund or un-mark it "
            "before deleting.",
        )

    logger.warning(
        "admin_hard_delete_order order_id=%s order_number=%s admin_id=%s",
        order.id,
        order.order_number,
        admin_id,
    )

    # Give back the stock it debited (a no-op if a cancel already did),
    # unless it shipped: that stock left with the parcel.
    from src.application.services.stock_service import restock_order
    from src.infrastructure.repositories.order_repository import OrderRepository

    entity = await OrderRepository(db).get_by_id(order_id)
    if entity is not None and entity.status not in (
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.RETURNED,
    ):
        await restock_order(db, entity, reason="admin_deleted")
    await AuditService(db).log(
        event_type="order.deleted_by_staff",
        action="delete",
        resource_type="order",
        resource_id=str(order.id),
        user_id=admin_id,
        tenant_id=order.tenant_id,
        store_id=order.store_id,
        details={"order_number": order.order_number, "actor_type": "staff"},
    )
    await db.execute(delete(OrderModel).where(OrderModel.id == order_id))
    await db.commit()


@router.post(
    "/{order_id}/mark-paid",
    response_model=SuccessResponse[AdminOrderDetail],
    summary="Mark an order paid (admin)",
    operation_id="admin_mark_order_paid",
)
async def mark_order_paid(
    order_id: Annotated[UUID, Path(description="Order ID")],
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Record money collected outside a gateway, exactly like the merchant's
    Mark-as-paid button (invoice, commission and email ride OrderPaidEvent)."""
    entity = await _load_entity(db, order_id)
    if entity.payment_status in (
        PaymentStatus.PAID,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    ) or entity.status in (
        OrderStatus.CANCELLED,
        OrderStatus.RETURNED,
        OrderStatus.REFUNDED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order is {entity.status.value} / {entity.payment_status.value}",
        )
    old_status = entity.status.value
    await store_orders.apply_manual_payment(entity, OrderRepository(db))
    await _close_live_intent(db, order_id)
    await _audit(db, admin_id, entity, "marked_paid", from_status=old_status)
    await db.commit()
    return SuccessResponse(
        data=_order_to_detail(await _load_order(db, order_id)),
        message="Order marked as paid",
    )


@router.post(
    "/{order_id}/confirm-deposit",
    response_model=SuccessResponse[AdminOrderDetail],
    summary="Confirm a COD deposit was received (admin)",
    operation_id="admin_confirm_order_deposit",
)
async def confirm_order_deposit(
    order_id: Annotated[UUID, Path(description="Order ID")],
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """PENDING_DEPOSIT -> CONFIRMED; the balance stays due on delivery."""
    entity = await _load_entity(db, order_id)
    if entity.status != OrderStatus.PENDING_DEPOSIT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order is {entity.status.value}, not pending_deposit",
        )
    entity.mark_as_paid(payment_id=f"ADMIN-{admin_id}", payment_method="manual")
    await OrderRepository(db).update(entity)
    await _close_live_intent(db, order_id)
    await _audit(db, admin_id, entity, "deposit_confirmed")
    await db.commit()
    return SuccessResponse(
        data=_order_to_detail(await _load_order(db, order_id)),
        message="Deposit confirmed",
    )


@router.post(
    "/{order_id}/resend-whatsapp",
    response_model=SuccessResponse,
    summary="Resend the WhatsApp order confirmation (admin)",
    operation_id="admin_resend_order_whatsapp",
)
async def resend_order_whatsapp(
    order_id: Annotated[UUID, Path(description="Order ID")],
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """The merchant's resend, every guard included."""
    entity = await _load_entity(db, order_id)
    result = await store_orders.resend_order_whatsapp(
        order_id,
        store=SimpleNamespace(id=entity.store_id),
        order_repo=OrderRepository(db),
        customer_repo=CustomerRepository(db),
        db=db,
    )
    await _audit(db, admin_id, entity, "whatsapp_resent", sent=result.data.get("sent"))
    await db.commit()
    return result


@router.get(
    "/{order_id}/activities",
    response_model=SuccessResponse[OrderActivitiesResponse],
    summary="Order activity stream (admin)",
    operation_id="admin_list_order_activities",
)
async def list_order_activities(
    order_id: Annotated[UUID, Path(description="Order ID")],
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    entity = await _load_entity(db, order_id)
    return await store_orders.list_order_activities(
        order_id,
        store=SimpleNamespace(id=entity.store_id),
        order_repo=OrderRepository(db),
        activity_repo=OrderActivityRepository(db),
        page=page,
        limit=limit,
    )


@router.post(
    "/{order_id}/notes",
    response_model=SuccessResponse[OrderActivityResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add a staff note to an order (admin)",
    operation_id="admin_create_order_note",
)
async def create_order_note(
    order_id: Annotated[UUID, Path(description="Order ID")],
    request: AdminOrderNoteRequest,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    entity = await _load_entity(db, order_id)
    result = await store_orders.create_order_comment(
        order_id,
        CreateOrderCommentRequest(content=request.body),
        store=SimpleNamespace(id=entity.store_id),
        user_id=admin_id,
        order_repo=OrderRepository(db),
        activity_repo=OrderActivityRepository(db),
    )
    await _audit(db, admin_id, entity, "note_added", activity_id=result.data.id)
    await db.commit()
    return result

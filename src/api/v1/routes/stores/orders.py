"""Order routes nested under stores.

URL: /stores/{store_id}/orders
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import (
    get_customer_repository,
    get_onboarding_repository,
    get_order_repository,
    get_store_repository,
    verify_store_ownership,
)
from src.api.dependencies.plan import require_order_limit
from src.api.dependencies.repositories import (
    get_funnel_event_repository,
    get_network_reputation_repository,
)
from src.application.services.cod_trust_service import (
    CodTrustDecision,
    LocationSignals,
    check_customer_trust,
)

logger = logging.getLogger(__name__)
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    CreateOrderRequest,
    OrderAddressResponse,
    OrderLineItemResponse,
    OrderListItemResponse,
    OrderResponse,
    PaginatedListResponse,
    UpdateOrderRequest,
    UpdateOrderStatusRequest,
)
from src.application.dto.order import (
    CreateOrderAddressDTO,
    CreateOrderDTO,
    CreateOrderLineItemDTO,
    UpdateOrderDTO,
    UpdateOrderStatusDTO,
)
from src.application.use_cases.orders import (
    CreateOrderUseCase,
    GetOrderUseCase,
    ListOrdersUseCase,
    UpdateOrderStatusUseCase,
    UpdateOrderUseCase,
)
from src.core.entities.store import Store
from src.infrastructure.events.setup import get_event_bus
from src.infrastructure.repositories import (
    CustomerRepository,
    OnboardingRepository,
    OrderRepository,
    StoreRepository,
)

router = APIRouter(prefix="/{store_id}/orders")


def _risk_level_from_score(score: int | None) -> str:
    if score is None:
        return "low"
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _suggested_action_from_decision(decision: CodTrustDecision) -> str:
    if decision.reason == "blocked_high_risk":
        return "cancel"
    if decision.reason == "warned_high_risk":
        return "whatsapp_confirm"
    return "auto_approve"


async def _persist_cod_trust_assessment(
    *,
    session,
    store: Store,
    order_id: UUID | None,
    order_number: str | None,
    customer_id: UUID,
    customer_repo: CustomerRepository,
    total_cents: int,
    currency: str,
    decision: CodTrustDecision,
) -> None:
    """Persist a RiskAssessmentModel row for a manual-order trust decision.

    Fail-open — fraud auditing must never break order creation.
    """
    try:
        from src.infrastructure.database.models.tenant.risk_assessment import (
            RiskAssessmentModel,
        )

        customer = await customer_repo.get_by_id(customer_id)
        customer_name = None
        customer_email = None
        if customer:
            customer_name = (
                f"{customer.first_name or ''} {customer.last_name or ''}".strip()
                or None
            )
            customer_email = str(customer.email) if customer.email else None

        score = decision.score if decision.score is not None else 0
        assessment = RiskAssessmentModel(
            tenant_id=store.tenant_id,
            store_id=store.id,
            order_id=order_id,
            order_number=order_number,
            customer_name=customer_name,
            customer_email=customer_email,
            total_cents=total_cents,
            currency=currency,
            payment_method="cod",
            risk_score=score,
            risk_level=_risk_level_from_score(decision.score),
            score_type="preliminary",
            suggested_action=_suggested_action_from_decision(decision),
            action_taken=decision.reason,
            action_taken_at=datetime.now(UTC),
            action_taken_by="cod_trust",
            factors=list(decision.factors or []),
            scored_at=datetime.now(UTC),
        )
        session.add(assessment)
        await session.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cod_trust_assessment_persist_failed: %s", exc)


def _order_address_to_response(address_dto) -> OrderAddressResponse:
    """Convert OrderAddressDTO to OrderAddressResponse."""
    return OrderAddressResponse(
        first_name=address_dto.first_name,
        last_name=address_dto.last_name,
        full_name=address_dto.full_name,
        address_line1=address_dto.address_line1,
        address_line2=address_dto.address_line2,
        city=address_dto.city,
        state=address_dto.state,
        postal_code=address_dto.postal_code,
        country=address_dto.country,
        phone=address_dto.phone,
    )


def _order_line_item_to_response(item_dto) -> OrderLineItemResponse:
    """Convert OrderLineItemDTO to OrderLineItemResponse."""
    return OrderLineItemResponse(
        product_id=str(item_dto.product_id),
        product_name=item_dto.product_name,
        variant_id=str(item_dto.variant_id) if item_dto.variant_id else None,
        variant_name=item_dto.variant_name,
        sku=item_dto.sku,
        quantity=item_dto.quantity,
        unit_price=item_dto.unit_price,
        total_price=item_dto.total_price,
    )


def _order_to_response(order_dto) -> OrderResponse:
    """Convert OrderDTO to OrderResponse."""
    return OrderResponse(
        id=str(order_dto.id),
        store_id=str(order_dto.store_id),
        customer_id=str(order_dto.customer_id),
        order_number=order_dto.order_number,
        line_items=[
            _order_line_item_to_response(item) for item in order_dto.line_items
        ],
        shipping_address=_order_address_to_response(order_dto.shipping_address),
        billing_address=_order_address_to_response(order_dto.billing_address)
        if order_dto.billing_address
        else None,
        status=order_dto.status,
        payment_status=order_dto.payment_status,
        fulfillment_status=order_dto.fulfillment_status,
        subtotal=order_dto.subtotal,
        shipping_cost=order_dto.shipping_cost,
        tax_amount=order_dto.tax_amount,
        discount_amount=order_dto.discount_amount,
        total=order_dto.total,
        currency=order_dto.currency,
        payment_method=order_dto.payment_method,
        payment_id=order_dto.payment_id,
        shipping_method=order_dto.shipping_method,
        tracking_number=order_dto.tracking_number,
        tracking_url=order_dto.tracking_url,
        notes=order_dto.notes,
        customer_notes=order_dto.customer_notes,
        item_count=order_dto.item_count,
        is_paid=order_dto.is_paid,
        can_be_cancelled=order_dto.can_be_cancelled,
        cancelled_at=str(order_dto.cancelled_at) if order_dto.cancelled_at else None,
        paid_at=str(order_dto.paid_at) if order_dto.paid_at else None,
        fulfilled_at=str(order_dto.fulfilled_at) if order_dto.fulfilled_at else None,
        shipped_at=str(order_dto.shipped_at) if order_dto.shipped_at else None,
        delivered_at=str(order_dto.delivered_at) if order_dto.delivered_at else None,
        created_at=str(order_dto.created_at),
        updated_at=str(order_dto.updated_at),
    )


def _order_list_item_to_response(order_dto) -> OrderListItemResponse:
    """Convert OrderListItemDTO to OrderListItemResponse."""
    return OrderListItemResponse(
        id=str(order_dto.id),
        order_number=order_dto.order_number,
        customer_id=str(order_dto.customer_id),
        customer_name=order_dto.customer_name,
        status=order_dto.status,
        payment_status=order_dto.payment_status,
        fulfillment_status=order_dto.fulfillment_status,
        total=order_dto.total,
        currency=order_dto.currency,
        item_count=order_dto.item_count,
        payment_method=order_dto.payment_method,
        created_at=str(order_dto.created_at),
    )


@router.post(
    "/",
    response_model=SuccessResponse[OrderResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new order",
    operation_id="create_order",
    dependencies=[Depends(require_order_limit())],
)
async def create_order(
    request: CreateOrderRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    network_repo: Annotated[object, Depends(get_network_reputation_repository)],
):
    """Create a new order for the store."""
    # ── COD Trust Network check (manual merchant orders) ──────────────
    # The merchant might enter an order received over WhatsApp / phone
    # call. We honour the same block/warn setting as storefront so the
    # merchant gets the same fraud-filter signal. Manual orders almost
    # never carry GPS coordinates → LocationSignals(None,...) which by
    # design adds the +15 no-location penalty (a reasonable signal —
    # the merchant didn't see the customer pin a map either).
    is_cod = (request.payment_method or "cod") == "cod"
    trust_decision: CodTrustDecision | None = None
    if is_cod:
        customer_phone = (
            request.shipping_address.phone if request.shipping_address else None
        )
        location_signals = LocationSignals()
        trust_decision = await check_customer_trust(
            phone=customer_phone,
            store_settings=store.settings,
            network_repo=network_repo,
            location=location_signals,
        )
        logger.info(
            "cod_trust_check_manual store=%s allowed=%s reason=%s score=%s",
            str(store.id),
            trust_decision.allowed,
            trust_decision.reason,
            trust_decision.score,
        )
        if not trust_decision.allowed:
            await _persist_cod_trust_assessment(
                session=order_repo.session,
                store=store,
                order_id=None,
                order_number=None,
                customer_id=request.customer_id,
                customer_repo=customer_repo,
                total_cents=0,
                currency=request.currency or "EGP",
                decision=trust_decision,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "cod_trust_blocked",
                    "message_ar": (
                        "هذا العميل مُعلَّم كعالي المخاطر في شبكة نمو. "
                        "يُفضَّل التحصيل المسبق."
                    ),
                    "message_en": (
                        "This customer is flagged as high-risk in the trust "
                        "network. Consider asking for a deposit before "
                        "creating a COD order."
                    ),
                },
            )

    use_case = CreateOrderUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
        customer_repository=customer_repo,
        onboarding_repository=onboarding_repo,
        event_bus=get_event_bus(),
    )

    # Convert line items
    line_items = [
        CreateOrderLineItemDTO(
            product_id=item.product_id,
            product_name=item.product_name,
            variant_id=item.variant_id,
            variant_name=item.variant_name,
            sku=item.sku,
            quantity=item.quantity,
            unit_price=item.unit_price,
        )
        for item in request.line_items
    ]

    # Convert addresses
    shipping_address = CreateOrderAddressDTO(
        first_name=request.shipping_address.first_name,
        last_name=request.shipping_address.last_name,
        address_line1=request.shipping_address.address_line1,
        address_line2=request.shipping_address.address_line2,
        city=request.shipping_address.city,
        state=request.shipping_address.state,
        postal_code=request.shipping_address.postal_code,
        country=request.shipping_address.country,
        phone=request.shipping_address.phone,
    )

    billing_address = None
    if request.billing_address:
        billing_address = CreateOrderAddressDTO(
            first_name=request.billing_address.first_name,
            last_name=request.billing_address.last_name,
            address_line1=request.billing_address.address_line1,
            address_line2=request.billing_address.address_line2,
            city=request.billing_address.city,
            state=request.billing_address.state,
            postal_code=request.billing_address.postal_code,
            country=request.billing_address.country,
            phone=request.billing_address.phone,
        )

    dto = CreateOrderDTO(
        customer_id=request.customer_id,
        line_items=line_items,
        shipping_address=shipping_address,
        billing_address=billing_address,
        shipping_cost=request.shipping_cost,
        tax_amount=request.tax_amount,
        discount_amount=request.discount_amount,
        currency=request.currency,
        payment_method=request.payment_method,
        shipping_method=request.shipping_method,
        customer_notes=request.customer_notes,
    )

    result = await use_case.execute(
        dto=dto,
        store_id=store.id,
        user_id=store.owner_id,
    )

    # Persist the allowed/warned COD trust assessment so the merchant
    # decisions feed includes manual orders (matches storefront parity).
    if (
        is_cod
        and trust_decision is not None
        and trust_decision.reason not in {"disabled", "no_phone", "lookup_error"}
    ):
        await _persist_cod_trust_assessment(
            session=order_repo.session,
            store=store,
            order_id=result.id,
            order_number=result.order_number,
            customer_id=request.customer_id,
            customer_repo=customer_repo,
            total_cents=result.total,
            currency=result.currency,
            decision=trust_decision,
        )

    return SuccessResponse(
        data=_order_to_response(result),
        message="Order created successfully",
    )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[OrderListItemResponse]],
    summary="List orders",
    operation_id="list_orders",
)
async def list_orders(
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    order_status: str | None = Query(None, alias="status"),
    payment_status: str | None = Query(None, description="Filter by payment status"),
    fulfillment_status: str | None = Query(
        None, description="Filter by fulfillment status"
    ),
    date_from: datetime | None = Query(
        None, description="Filter orders from this date (ISO 8601)"
    ),
    date_to: datetime | None = Query(
        None, description="Filter orders until this date (ISO 8601)"
    ),
    search: str | None = Query(None),
    customer_id: str | None = Query(None, description="Filter by customer ID"),
):
    """List orders for a store with optional filtering and pagination.

    For the specific case of "orders with an InstaPay proof awaiting
    merchant review", use the dedicated endpoint
    ``GET /stores/{store_id}/orders/pending-instapay-review`` instead
    of a query param on this route. It runs a self-join on
    ``payment_proofs`` so the server returns only rows where the
    *latest* proof is ``awaiting_review`` — a condition this list
    endpoint's repository layer can't express today.
    """
    use_case = ListOrdersUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
        customer_repository=customer_repo,
    )

    result = await use_case.execute(
        store_id=store.id,
        user_id=store.owner_id,
        page=page,
        limit=limit,
        status=order_status,
        payment_status=payment_status,
        fulfillment_status=fulfillment_status,
        date_from=date_from,
        date_to=date_to,
        search=search,
        customer_id=UUID(customer_id) if customer_id else None,
    )

    orders = [_order_list_item_to_response(order) for order in result.orders]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=orders,
            total=result.total,
            page=result.page,
            page_size=result.limit,
            total_pages=result.total_pages,
        ),
        message="Orders retrieved successfully",
    )


@router.get(
    "/{order_id}",
    response_model=SuccessResponse[OrderResponse],
    summary="Get order by ID",
    operation_id="get_order",
)
async def get_order(
    order_id: Annotated[UUID, Path(description="Order ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Get order details by ID."""
    use_case = GetOrderUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
    )

    result = await use_case.execute(
        order_id=order_id,
        store_id=store.id,
        user_id=store.owner_id,
    )

    return SuccessResponse(
        data=_order_to_response(result),
        message="Order retrieved successfully",
    )


@router.patch(
    "/{order_id}",
    response_model=SuccessResponse[OrderResponse],
    summary="Update order",
    operation_id="update_order",
)
async def update_order(
    order_id: Annotated[UUID, Path(description="Order ID")],
    request: UpdateOrderRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update order details."""
    use_case = UpdateOrderUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
    )

    # Convert addresses if provided
    shipping_address = None
    if request.shipping_address:
        shipping_address = CreateOrderAddressDTO(
            first_name=request.shipping_address.first_name,
            last_name=request.shipping_address.last_name,
            address_line1=request.shipping_address.address_line1,
            address_line2=request.shipping_address.address_line2,
            city=request.shipping_address.city,
            state=request.shipping_address.state,
            postal_code=request.shipping_address.postal_code,
            country=request.shipping_address.country,
            phone=request.shipping_address.phone,
        )

    billing_address = None
    if request.billing_address:
        billing_address = CreateOrderAddressDTO(
            first_name=request.billing_address.first_name,
            last_name=request.billing_address.last_name,
            address_line1=request.billing_address.address_line1,
            address_line2=request.billing_address.address_line2,
            city=request.billing_address.city,
            state=request.billing_address.state,
            postal_code=request.billing_address.postal_code,
            country=request.billing_address.country,
            phone=request.billing_address.phone,
        )

    dto = UpdateOrderDTO(
        shipping_address=shipping_address,
        billing_address=billing_address,
        shipping_cost=request.shipping_cost,
        tax_amount=request.tax_amount,
        discount_amount=request.discount_amount,
        payment_method=request.payment_method,
        shipping_method=request.shipping_method,
        tracking_number=request.tracking_number,
        notes=request.notes,
        customer_notes=request.customer_notes,
    )

    result = await use_case.execute(
        order_id=order_id,
        dto=dto,
        store_id=store.id,
        user_id=store.owner_id,
    )

    return SuccessResponse(
        data=_order_to_response(result),
        message="Order updated successfully",
    )


@router.patch(
    "/{order_id}/status",
    response_model=SuccessResponse[OrderResponse],
    summary="Update order status",
    operation_id="update_order_status",
)
async def update_order_status(
    order_id: Annotated[UUID, Path(description="Order ID")],
    request: UpdateOrderStatusRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    network_repo: Annotated[object, Depends(get_network_reputation_repository)],
    funnel_repo: Annotated[object, Depends(get_funnel_event_repository)],
):
    """Update order status."""
    use_case = UpdateOrderStatusUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
        customer_repository=customer_repo,
        event_bus=get_event_bus(),
        network_repository=network_repo,
        funnel_repository=funnel_repo,
    )

    dto = UpdateOrderStatusDTO(
        status=request.status,
        reason=request.reason,
    )

    result = await use_case.execute(
        order_id=order_id,
        dto=dto,
        store_id=store.id,
        user_id=store.owner_id,
    )

    return SuccessResponse(
        data=_order_to_response(result),
        message="Order status updated successfully",
    )


@router.delete(
    "/{order_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel order",
    operation_id="cancel_order",
)
async def cancel_order(
    order_id: Annotated[UUID, Path(description="Order ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    network_repo: Annotated[object, Depends(get_network_reputation_repository)],
    funnel_repo: Annotated[object, Depends(get_funnel_event_repository)],
    reason: str | None = Query(None, description="Cancellation reason"),
):
    """Cancel an order."""
    use_case = UpdateOrderStatusUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
        customer_repository=customer_repo,
        event_bus=get_event_bus(),
        network_repository=network_repo,
        funnel_repository=funnel_repo,
    )

    dto = UpdateOrderStatusDTO(
        status="cancelled",
        reason=reason,
    )

    await use_case.execute(
        order_id=order_id,
        dto=dto,
        store_id=store.id,
        user_id=store.owner_id,
    )

    return None


@router.post(
    "/{order_id}/mark-paid",
    response_model=SuccessResponse[OrderResponse],
    summary="Mark order as paid",
    operation_id="mark_order_paid",
)
async def mark_order_paid(
    order_id: Annotated[UUID, Path(description="Order ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Manually mark an order's payment as paid (e.g. COD collected)."""
    from src.core.entities.order import PaymentStatus

    order = await order_repo.get_by_id(order_id)
    if not order or order.store_id != store.id:
        from src.core.exceptions import EntityNotFoundError

        raise EntityNotFoundError("Order", str(order_id))

    if order.payment_status == PaymentStatus.PAID:
        return SuccessResponse(
            data=_order_to_response(OrderDTO.from_entity(order)),
            message="Order is already marked as paid",
        )

    order.payment_status = PaymentStatus.PAID
    order.paid_at = datetime.now(UTC)
    order.touch()
    updated = await order_repo.update(order)

    try:
        from src.core.events.order_events import OrderPaidEvent

        get_event_bus().publish(
            OrderPaidEvent(
                order_id=updated.id,
                order_number=updated.order_number,
                store_id=updated.store_id,
                customer_id=updated.customer_id,
                payment_method=updated.payment_method,
                total=float(updated.total),
            )
        )
    except Exception:
        pass

    return SuccessResponse(
        data=_order_to_response(OrderDTO.from_entity(updated)),
        message="Order marked as paid",
    )


# ============================================================================
# Timeline & Bulk endpoints
# ============================================================================

from src.api.v1.schemas.tenant.order import (
    BulkUpdateOrderStatusRequest,
    BulkUpdateOrderStatusResponse,
    OrderTimelineEvent,
    OrderTimelineResponse,
)
from src.application.dto.order import OrderDTO


def _build_timeline(order_dto: OrderDTO) -> list[OrderTimelineEvent]:
    """Construct a chronological timeline from order timestamps."""
    events: list[OrderTimelineEvent] = []

    events.append(
        OrderTimelineEvent(
            timestamp=str(order_dto.created_at),
            status="pending",
            description="Order placed",
        )
    )

    if order_dto.paid_at:
        events.append(
            OrderTimelineEvent(
                timestamp=str(order_dto.paid_at),
                status="paid",
                description=f"Payment received via {order_dto.payment_method or 'unknown'}",
            )
        )

    if order_dto.status in ("processing", "shipped", "delivered", "fulfilled"):
        events.append(
            OrderTimelineEvent(
                timestamp=str(order_dto.updated_at),
                status="processing",
                description="Order is being processed",
            )
        )

    if order_dto.shipped_at:
        desc = "Order shipped"
        if order_dto.tracking_number:
            desc += f" (tracking: {order_dto.tracking_number})"
        events.append(
            OrderTimelineEvent(
                timestamp=str(order_dto.shipped_at),
                status="shipped",
                description=desc,
            )
        )

    if order_dto.fulfilled_at:
        events.append(
            OrderTimelineEvent(
                timestamp=str(order_dto.fulfilled_at),
                status="fulfilled",
                description="Order fulfilled",
            )
        )

    if order_dto.delivered_at:
        events.append(
            OrderTimelineEvent(
                timestamp=str(order_dto.delivered_at),
                status="delivered",
                description="Order delivered",
            )
        )

    if order_dto.cancelled_at:
        events.append(
            OrderTimelineEvent(
                timestamp=str(order_dto.cancelled_at),
                status="cancelled",
                description="Order cancelled",
            )
        )

    if order_dto.status == "refunded":
        events.append(
            OrderTimelineEvent(
                timestamp=str(order_dto.updated_at),
                status="refunded",
                description="Order refunded",
            )
        )

    return events


@router.get(
    "/{order_id}/timeline",
    response_model=SuccessResponse[OrderTimelineResponse],
    summary="Get order timeline",
    operation_id="get_order_timeline",
)
async def get_order_timeline(
    order_id: Annotated[UUID, Path(description="Order ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Get the timeline of status changes for an order."""
    use_case = GetOrderUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
    )

    result = await use_case.execute(
        order_id=order_id,
        store_id=store.id,
        user_id=store.owner_id,
    )

    timeline = _build_timeline(result)

    return SuccessResponse(
        data=OrderTimelineResponse(
            order_id=str(result.id),
            order_number=result.order_number,
            events=timeline,
        ),
        message="Order timeline retrieved successfully",
    )


@router.post(
    "/bulk-status",
    response_model=SuccessResponse[BulkUpdateOrderStatusResponse],
    summary="Bulk update order statuses",
    operation_id="bulk_update_order_status",
)
async def bulk_update_order_status(
    request: BulkUpdateOrderStatusRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    network_repo: Annotated[object, Depends(get_network_reputation_repository)],
    funnel_repo: Annotated[object, Depends(get_funnel_event_repository)],
):
    """Update the status of multiple orders at once."""
    use_case = UpdateOrderStatusUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
        customer_repository=customer_repo,
        event_bus=get_event_bus(),
        network_repository=network_repo,
        funnel_repository=funnel_repo,
    )

    updated = 0
    failed = 0
    errors: list[dict] = []

    for oid in request.order_ids:
        try:
            dto = UpdateOrderStatusDTO(
                status=request.status,
                reason=request.reason,
            )
            await use_case.execute(
                order_id=oid,
                dto=dto,
                store_id=store.id,
                user_id=store.owner_id,
            )
            updated += 1
        except Exception as exc:
            failed += 1
            errors.append({"order_id": str(oid), "error": str(exc)})

    return SuccessResponse(
        data=BulkUpdateOrderStatusResponse(
            updated=updated,
            failed=failed,
            errors=errors,
        ),
        message=f"Bulk status update completed: {updated} updated, {failed} failed",
    )


# ============================================================================
# Notification endpoints
# ============================================================================


@router.post(
    "/{order_id}/resend-email",
    response_model=SuccessResponse,
    summary="Resend order status email",
    operation_id="resend_order_email",
)
async def resend_order_email(
    order_id: Annotated[UUID, Path(description="Order ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Resend the status email for an order's current status."""
    from src.core.exceptions import EntityNotFoundError
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.notifications import (
        order_status_email,
    )

    order = await order_repo.get_by_id(order_id)
    if not order or order.store_id != store.id:
        raise EntityNotFoundError("Order", str(order_id))

    customer = await customer_repo.get_by_id(order.customer_id)
    if not customer or not customer.email:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Customer has no email address")

    template = order_status_email(
        status=order.status.value,
        order_number=order.order_number,
        store_name=store.name,
        customer_name=customer.full_name,
        tracking_number=order.tracking_number,
        carrier=order.shipping_method,
        language=store.default_language or "en",
    )

    if not template:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail=f"No email template for status '{order.status.value}'",
        )

    from src.core.interfaces.services.email_service import EmailMessage

    service = ResendEmailService()
    await service.send_email(
        EmailMessage(
            to=str(customer.email),
            subject=template["subject"],
            html_content=template["html"],
        )
    )

    return SuccessResponse(
        data={"order_id": str(order_id), "status": order.status.value},
        message="Email resent successfully",
    )


@router.get(
    "/{order_id}/email-preview",
    summary="Preview order status email",
    operation_id="preview_order_email",
)
async def preview_order_email(
    order_id: Annotated[UUID, Path(description="Order ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    email_status: str | None = Query(
        None,
        alias="status",
        description="Preview a specific status (defaults to current)",
    ),
):
    """Preview the email HTML that would be sent for an order status.

    Useful for merchants to review notification content before sending.
    """
    from src.infrastructure.external_services.resend.email_templates.notifications import (
        order_status_email,
    )

    order = await order_repo.get_by_id(order_id)
    if not order or order.store_id != store.id:
        from src.core.exceptions import EntityNotFoundError

        raise EntityNotFoundError("Order", str(order_id))

    customer = await customer_repo.get_by_id(order.customer_id)
    target_status = email_status or order.status.value

    template = order_status_email(
        status=target_status,
        order_number=order.order_number,
        store_name=store.name,
        customer_name=customer.full_name if customer else None,
        tracking_number=order.tracking_number,
        carrier=order.shipping_method,
        language=store.default_language or "en",
    )

    if not template:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail=f"No email template for status '{target_status}'",
        )

    from fastapi.responses import HTMLResponse

    return HTMLResponse(content=template["html"])

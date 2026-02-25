"""Storefront checkout route.

URL: /storefront/store/{store_id}/checkout

Creates an order from the submitted line items, calculates totals
using live product prices, and optionally initiates payment.
"""

import json
import logging
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_coupon_repository,
    get_customer_repository,
    get_order_repository,
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.storefront.checkout import CheckoutRequest, CheckoutResponse
from src.application.dto.order import (
    CreateOrderAddressDTO,
    CreateOrderDTO,
    CreateOrderLineItemDTO,
)
from src.config import settings
from src.core.entities.customer import Customer
from src.core.entities.product import ProductStatus
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.repositories import (
    CouponRepository,
    CustomerRepository,
    OrderRepository,
    ProductRepository,
    StoreRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)
IDEMPOTENCY_TTL_SECONDS = 86_400  # 24 hours


@router.post(
    "/checkout",
    response_model=SuccessResponse[CheckoutResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create order from checkout",
    operation_id="checkout",
)
async def checkout(
    response: Response,
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CheckoutRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Process checkout for the authenticated customer.

    1. Validates all products exist, are active, in stock, and belong to the store.
    2. Resolves live prices from the product catalog (never trusts client prices).
    3. Creates an Order in PENDING status.
    4. Returns an optional payment_url when the payment method requires redirect.
    """
    # ── Idempotency check ──────────────────────────────────────────────
    if idempotency_key and _cache_service:
        cache_key = (
            f"checkout:idempotency:{store_id}:{current_customer.id}:{idempotency_key}"
        )
        cached = await _cache_service.get(cache_key)
        if cached:
            logger.info(
                f"Idempotent checkout hit: key={idempotency_key}, "
                f"customer={current_customer.id}"
            )
            response.status_code = status.HTTP_200_OK
            return SuccessResponse(
                data=CheckoutResponse(**json.loads(cached)),
                message="Order already created",
            )

    # Verify the customer belongs to this store
    if current_customer.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer does not belong to this store",
        )

    # Validate store
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    # Build line items with server-side price resolution
    line_items: list[CreateOrderLineItemDTO] = []
    for item in request.line_items:
        product = await product_repo.get_by_id(item.product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {item.product_id} not found",
            )
        if product.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {product.name} does not belong to this store",
            )
        if product.status != ProductStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {product.name} is not available",
            )
        if product.quantity < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for {product.name} (available: {product.quantity})",
            )

        line_items.append(
            CreateOrderLineItemDTO(
                product_id=product.id,
                product_name=product.name,
                sku=product.sku,
                quantity=item.quantity,
                unit_price=product.price.cents,
                variant_id=item.variant_id,
            )
        )

    # Build address DTOs
    addr = request.shipping_address
    shipping_address = CreateOrderAddressDTO(
        first_name=addr.first_name,
        last_name=addr.last_name,
        address_line1=addr.address_line1,
        address_line2=addr.address_line2,
        city=addr.city,
        state=addr.state,
        postal_code=addr.postal_code,
        country=addr.country,
        phone=addr.phone,
    )

    billing_address = None
    if request.billing_address:
        b = request.billing_address
        billing_address = CreateOrderAddressDTO(
            first_name=b.first_name,
            last_name=b.last_name,
            address_line1=b.address_line1,
            address_line2=b.address_line2,
            city=b.city,
            state=b.state,
            postal_code=b.postal_code,
            country=b.country,
            phone=b.phone,
        )

    currency = store.default_currency.value if store.default_currency else "EGP"

    dto = CreateOrderDTO(
        customer_id=current_customer.id,
        line_items=line_items,
        shipping_address=shipping_address,
        billing_address=billing_address,
        currency=currency,
        payment_method=request.payment_method,
        shipping_method=request.shipping_method,
        customer_notes=request.customer_notes,
    )

    # Create order via the existing use case
    # We pass store.owner_id to satisfy the authorization check inside
    # CreateOrderUseCase (it verifies user_id == store.owner_id).
    # For customer-initiated checkout we bypass that by calling the
    # repository directly with the same logic.
    order_number = await order_repo.get_next_order_number(store_id)

    from src.core.entities.order import (
        Order,
        OrderLineItem,
        OrderShippingAddress,
        OrderStatus,
        PaymentStatus,
    )

    order_line_items = []
    subtotal = 0
    for li in line_items:
        total_price = li.unit_price * li.quantity
        subtotal += total_price
        order_line_items.append(
            OrderLineItem(
                product_id=li.product_id,
                product_name=li.product_name,
                variant_id=li.variant_id,
                sku=li.sku,
                quantity=li.quantity,
                unit_price=li.unit_price,
                total_price=total_price,
            )
        )

    ship_addr = OrderShippingAddress(
        first_name=shipping_address.first_name,
        last_name=shipping_address.last_name,
        address_line1=shipping_address.address_line1,
        address_line2=shipping_address.address_line2,
        city=shipping_address.city,
        state=shipping_address.state,
        postal_code=shipping_address.postal_code,
        country=shipping_address.country,
        phone=shipping_address.phone,
    )

    bill_addr = None
    if billing_address:
        bill_addr = OrderShippingAddress(
            first_name=billing_address.first_name,
            last_name=billing_address.last_name,
            address_line1=billing_address.address_line1,
            address_line2=billing_address.address_line2,
            city=billing_address.city,
            state=billing_address.state,
            postal_code=billing_address.postal_code,
            country=billing_address.country,
            phone=billing_address.phone,
        )

    # Apply coupon if provided (with row-level lock to prevent concurrent bypass)
    discount_amount = 0
    coupon_code = None
    coupon_id = None

    if request.coupon_code:
        from src.application.use_cases.coupons.apply_coupon import ApplyCouponUseCase

        apply_coupon = ApplyCouponUseCase(coupon_repository=coupon_repo)
        coupon_result = await apply_coupon.execute(
            store_id=store_id,
            code=request.coupon_code,
            order_amount=Decimal(str(subtotal)),
            for_update=True,
        )
        discount_amount = int(coupon_result.discount_amount)
        coupon_code = coupon_result.code
        coupon_id = coupon_result.coupon_id

    # Atomically deduct stock BEFORE creating the order.
    # Uses conditional UPDATE (WHERE quantity >= needed) so concurrent
    # checkouts cannot oversell. If the transaction rolls back, stock
    # is automatically restored.
    for li in line_items:
        success = await product_repo.deduct_stock(li.product_id, li.quantity)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Insufficient stock for {li.product_name}. Please refresh and try again.",
            )

    total = subtotal + dto.shipping_cost + dto.tax_amount - discount_amount

    order = Order(
        store_id=store_id,
        tenant_id=store.tenant_id,
        customer_id=current_customer.id,
        order_number=order_number,
        line_items=order_line_items,
        shipping_address=ship_addr,
        billing_address=bill_addr,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        subtotal=subtotal,
        shipping_cost=dto.shipping_cost,
        tax_amount=dto.tax_amount,
        discount_amount=discount_amount,
        coupon_code=coupon_code,
        coupon_id=coupon_id,
        total=total,
        currency=currency,
        payment_method=request.payment_method,
        shipping_method=request.shipping_method,
        customer_notes=request.customer_notes,
    )

    created_order = await order_repo.create(order)

    # Update customer lifetime stats
    current_customer.total_orders = (current_customer.total_orders or 0) + 1
    current_customer.total_spent = (
        current_customer.total_spent or 0
    ) + created_order.total
    await customer_repo.update(current_customer)

    # Build payment URL if applicable
    payment_url: str | None = None
    # Payment initiation would go here based on request.payment_method.
    # For COD orders, payment_url stays None.

    logger.info(
        f"Checkout completed: order={created_order.order_number}, "
        f"customer={current_customer.id}, total={created_order.total} {currency}"
    )

    # Dispatch async order-confirmation notifications (non-blocking)
    try:
        from src.infrastructure.messaging.tasks.notification_tasks import (
            send_order_confirmation_email_task,
            send_whatsapp_order_confirmation_task,
        )

        prefs = current_customer.metadata.get("notification_preferences", {})
        email_prefs = prefs.get("email", {})
        whatsapp_prefs = prefs.get("whatsapp", {})

        customer_email = str(current_customer.email) if current_customer.email else None
        customer_phone = str(current_customer.phone) if current_customer.phone else None

        total_display = f"{currency} {created_order.total / 100:.2f}"

        # Email: order confirmation
        if customer_email and email_prefs.get("order_confirmation", True):
            order_details = {
                "items": [
                    {
                        "name": li.product_name,
                        "quantity": li.quantity,
                        "price": li.unit_price / 100,
                    }
                    for li in order_line_items
                ],
                "total": created_order.total / 100,
            }
            send_order_confirmation_email_task.delay(
                email=customer_email,
                order_number=created_order.order_number,
                order_details=order_details,
                language=store.default_language,
            )

        # WhatsApp: order confirmation
        if customer_phone and whatsapp_prefs.get("order_confirmation", True):
            send_whatsapp_order_confirmation_task.delay(
                phone=customer_phone,
                customer_name=current_customer.full_name,
                order_number=created_order.order_number,
                total=total_display,
                store_name=store.name,
                language=store.default_language,
            )
    except Exception as e:
        logger.warning(f"Failed to dispatch checkout notifications: {e}")

    # Merchant onboarding: send first-order email if this is order #1
    try:
        total_orders = await order_repo.count_by_store(store_id)
        if total_orders == 1:
            from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
                send_first_order_email_task,
            )

            merchant_email = store.contact_email
            if merchant_email:
                send_first_order_email_task.delay(
                    email=merchant_email,
                    merchant_name=store.name,
                    order_number=created_order.order_number,
                    total=f"{currency} {created_order.total / 100:.2f}",
                    language=store.default_language,
                )
    except Exception as e:
        logger.warning(f"Failed to dispatch first-order onboarding email: {e}")

    # Clear the customer's cart only after the entire checkout succeeds
    from src.api.v1.routes.storefront.cart import _carts

    _carts.pop(current_customer.id, None)

    checkout_response = CheckoutResponse(
        order_id=str(created_order.id),
        order_number=created_order.order_number,
        total=created_order.total,
        currency=created_order.currency,
        payment_status=created_order.payment_status.value,
        payment_url=payment_url,
    )

    # ── Cache response for idempotency ───────────────────────────────
    if idempotency_key and _cache_service:
        cache_key = (
            f"checkout:idempotency:{store_id}:{current_customer.id}:{idempotency_key}"
        )
        await _cache_service.set(
            cache_key,
            checkout_response.model_dump_json(),
            expire=IDEMPOTENCY_TTL_SECONDS,
        )

    return SuccessResponse(
        data=checkout_response,
        message="Order created successfully",
    )

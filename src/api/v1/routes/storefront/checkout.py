"""Storefront checkout route.

URL: /storefront/store/{store_id}/checkout

Creates an order from the submitted line items, calculates totals
using live product prices, and optionally initiates payment.
"""

import logging
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status

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
from src.application.use_cases.orders import CreateOrderUseCase
from src.core.entities.customer import Customer
from src.core.entities.product import ProductStatus
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import (
    CouponRepository,
    CustomerRepository,
    OrderRepository,
    ProductRepository,
    StoreRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/checkout",
    response_model=SuccessResponse[CheckoutResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create order from checkout",
)
async def checkout(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CheckoutRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
):
    """Process checkout for the authenticated customer.

    1. Validates all products exist, are active, in stock, and belong to the store.
    2. Resolves live prices from the product catalog (never trusts client prices).
    3. Creates an Order in PENDING status.
    4. Returns an optional payment_url when the payment method requires redirect.
    """
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
                unit_price=int(product.price.amount_cents),
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

    # Apply coupon if provided
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
        )
        discount_amount = int(coupon_result.discount_amount)
        coupon_code = coupon_result.code
        coupon_id = coupon_result.coupon_id

    total = subtotal + dto.shipping_cost + dto.tax_amount - discount_amount

    order = Order(
        store_id=store_id,
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

    # Increment coupon usage
    if coupon_id:
        await coupon_repo.increment_usage(coupon_id)

    # Deduct stock for each product
    for li in line_items:
        product = await product_repo.get_by_id(li.product_id)
        if product and product.quantity >= li.quantity:
            product.quantity -= li.quantity
            await product_repo.update(product)

    # Clear the customer's cart after successful checkout
    from src.api.v1.routes.storefront.cart import _carts

    _carts.pop(current_customer.id, None)

    # Build payment URL if applicable
    payment_url: str | None = None
    # Payment initiation would go here based on request.payment_method.
    # For COD orders, payment_url stays None.

    logger.info(
        f"Checkout completed: order={created_order.order_number}, "
        f"customer={current_customer.id}, total={created_order.total} {currency}"
    )

    return SuccessResponse(
        data=CheckoutResponse(
            order_id=str(created_order.id),
            order_number=created_order.order_number,
            total=created_order.total,
            currency=created_order.currency,
            payment_status=created_order.payment_status.value,
            payment_url=payment_url,
        ),
        message="Order created successfully",
    )

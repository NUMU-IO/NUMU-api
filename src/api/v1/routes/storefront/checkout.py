"""Checkout routes for authenticated customers.

URL: /storefront/me/checkout

Converts the customer's cart into an order.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_cart_repository,
    get_order_repository,
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.storefront.checkout import CheckoutAddressRequest, CheckoutRequest
from src.api.v1.schemas.tenant.order import (
    OrderAddressResponse,
    OrderLineItemResponse,
    OrderResponse,
)
from src.application.dto.checkout import CheckoutAddressDTO, CheckoutDTO
from src.application.use_cases.storefront.checkout import CheckoutUseCase
from src.core.entities.customer import Customer
from src.infrastructure.repositories import (
    CartRepository,
    OrderRepository,
    ProductRepository,
    StoreRepository,
)

router = APIRouter()


def _address_request_to_dto(addr: CheckoutAddressRequest) -> CheckoutAddressDTO:
    """Convert address request to DTO."""
    return CheckoutAddressDTO(
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


def _order_dto_to_response(dto) -> OrderResponse:
    """Convert OrderDTO to OrderResponse."""
    return OrderResponse(
        id=str(dto.id),
        store_id=str(dto.store_id),
        customer_id=str(dto.customer_id),
        order_number=dto.order_number,
        line_items=[
            OrderLineItemResponse(
                product_id=str(item.product_id),
                product_name=item.product_name,
                variant_id=str(item.variant_id) if item.variant_id else None,
                variant_name=item.variant_name,
                sku=item.sku,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_price=item.total_price,
            )
            for item in dto.line_items
        ],
        shipping_address=OrderAddressResponse(
            first_name=dto.shipping_address.first_name,
            last_name=dto.shipping_address.last_name,
            full_name=dto.shipping_address.full_name,
            address_line1=dto.shipping_address.address_line1,
            address_line2=dto.shipping_address.address_line2,
            city=dto.shipping_address.city,
            state=dto.shipping_address.state,
            postal_code=dto.shipping_address.postal_code,
            country=dto.shipping_address.country,
            phone=dto.shipping_address.phone,
        ),
        billing_address=OrderAddressResponse(
            first_name=dto.billing_address.first_name,
            last_name=dto.billing_address.last_name,
            full_name=dto.billing_address.full_name,
            address_line1=dto.billing_address.address_line1,
            address_line2=dto.billing_address.address_line2,
            city=dto.billing_address.city,
            state=dto.billing_address.state,
            postal_code=dto.billing_address.postal_code,
            country=dto.billing_address.country,
            phone=dto.billing_address.phone,
        ) if dto.billing_address else None,
        status=dto.status,
        payment_status=dto.payment_status,
        fulfillment_status=dto.fulfillment_status,
        subtotal=dto.subtotal,
        shipping_cost=dto.shipping_cost,
        tax_amount=dto.tax_amount,
        discount_amount=dto.discount_amount,
        total=dto.total,
        currency=dto.currency,
        payment_method=dto.payment_method,
        payment_id=dto.payment_id,
        shipping_method=dto.shipping_method,
        tracking_number=dto.tracking_number,
        tracking_url=dto.tracking_url,
        notes=dto.notes,
        customer_notes=dto.customer_notes,
        item_count=dto.item_count,
        is_paid=dto.is_paid,
        can_be_cancelled=dto.can_be_cancelled,
        cancelled_at=str(dto.cancelled_at) if dto.cancelled_at else None,
        paid_at=str(dto.paid_at) if dto.paid_at else None,
        fulfilled_at=str(dto.fulfilled_at) if dto.fulfilled_at else None,
        shipped_at=str(dto.shipped_at) if dto.shipped_at else None,
        delivered_at=str(dto.delivered_at) if dto.delivered_at else None,
        created_at=str(dto.created_at),
        updated_at=str(dto.updated_at),
    )


@router.post(
    "/checkout",
    response_model=SuccessResponse[OrderResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Checkout - convert cart to order",
)
async def checkout(
    request: CheckoutRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Convert the customer's cart into an order.

    This performs an atomic checkout:
    1. Validates all cart items are in stock (at current prices)
    2. Creates an order with line items
    3. Deducts inventory
    4. Clears the cart
    """
    use_case = CheckoutUseCase(
        cart_repository=cart_repo,
        order_repository=order_repo,
        product_repository=product_repo,
        store_repository=store_repo,
    )

    dto = CheckoutDTO(
        shipping_address=_address_request_to_dto(request.shipping_address),
        billing_address=_address_request_to_dto(request.billing_address) if request.billing_address else None,
        shipping_cost=request.shipping_cost,
        tax_amount=request.tax_amount,
        discount_amount=request.discount_amount,
        currency=request.currency,
        payment_method=request.payment_method,
        shipping_method=request.shipping_method,
        customer_notes=request.customer_notes,
    )

    result = await use_case.execute(
        store_id=current_customer.store_id,
        customer_id=current_customer.id,
        dto=dto,
    )

    return SuccessResponse(
        data=_order_dto_to_response(result),
        message="Order created successfully",
    )

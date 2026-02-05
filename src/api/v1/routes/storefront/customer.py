"""Authenticated customer routes.

URL: /storefront/me/...

These routes require customer authentication and provide:
- Profile management
- Password change
- Address management
- Order history
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_customer_address_repository,
    get_customer_repository,
    get_order_repository,
    get_store_repository,
)
from src.api.dependencies.services import get_password_service
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.customer import (
    CreateAddressRequest,
    CustomerAddressListResponse,
    CustomerAddressResponse,
    CustomerChangePasswordRequest,
    CustomerResponse,
    CustomerUpdateProfileRequest,
    UpdateAddressRequest,
)
from src.application.dto.customer import (
    CreateAddressDTO,
    CustomerChangePasswordDTO,
    CustomerUpdateProfileDTO,
    UpdateAddressDTO,
)
from src.application.use_cases.customers import (
    ChangeCustomerPasswordUseCase,
    UpdateCustomerProfileUseCase,
)
from src.application.use_cases.customers.addresses import (
    CreateAddressUseCase,
    DeleteAddressUseCase,
    ListAddressesUseCase,
    SetDefaultAddressUseCase,
    UpdateAddressUseCase,
)
from src.core.entities.customer import Customer
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.external_services import PasswordService
from src.infrastructure.repositories import (
    CustomerAddressRepository,
    CustomerRepository,
    OrderRepository,
    StoreRepository,
)

router = APIRouter()


# ============================================================================
# Profile Routes
# ============================================================================


@router.get(
    "/profile",
    response_model=SuccessResponse[CustomerResponse],
    summary="Get customer profile",
)
async def get_customer_profile(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
):
    """Get current customer profile."""
    result = current_customer

    return SuccessResponse(
        data=CustomerResponse(
            id=result.id,
            store_id=result.store_id,
            email=result.email,
            first_name=result.first_name,
            last_name=result.last_name,
            full_name=result.full_name,
            phone=result.phone,
            accepts_marketing=result.accepts_marketing,
            is_verified=result.is_verified,
            total_orders=result.total_orders,
            total_spent=result.total_spent,
            default_address_id=result.default_address_id,
            created_at=str(result.created_at) if result.created_at else None,
            updated_at=str(result.updated_at) if result.updated_at else None,
        ),
        message="Customer profile retrieved successfully",
    )


@router.put(
    "/profile",
    response_model=SuccessResponse[CustomerResponse],
    summary="Update customer profile",
)
async def update_customer_profile(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    request: CustomerUpdateProfileRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Update current customer profile."""
    use_case = UpdateCustomerProfileUseCase(customer_repository=customer_repo)

    dto = CustomerUpdateProfileDTO(
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
        accepts_marketing=request.accepts_marketing,
    )

    result = await use_case.execute(current_customer.id, dto)

    return SuccessResponse(
        data=CustomerResponse(
            id=result.id,
            store_id=result.store_id,
            email=result.email,
            first_name=result.first_name,
            last_name=result.last_name,
            full_name=result.full_name,
            phone=result.phone,
            accepts_marketing=result.accepts_marketing,
            is_verified=result.is_verified,
            total_orders=result.total_orders,
            total_spent=result.total_spent,
            default_address_id=result.default_address_id,
            created_at=str(result.created_at) if result.created_at else None,
            updated_at=str(result.updated_at) if result.updated_at else None,
        ),
        message="Customer profile updated successfully",
    )


@router.put(
    "/password",
    response_model=SuccessResponse[dict],
    summary="Change customer password",
)
async def change_customer_password(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    request: CustomerChangePasswordRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
):
    """Change current customer password."""
    use_case = ChangeCustomerPasswordUseCase(
        customer_repository=customer_repo,
        password_service=password_service,
    )

    dto = CustomerChangePasswordDTO(
        current_password=request.current_password,
        new_password=request.new_password,
    )

    await use_case.execute(current_customer.id, dto)

    return SuccessResponse(
        data={"success": True},
        message="Password changed successfully",
    )


# ============================================================================
# Address Routes
# ============================================================================


@router.get(
    "/addresses",
    response_model=SuccessResponse[CustomerAddressListResponse],
    summary="List customer addresses",
)
async def list_customer_addresses(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    address_repo: Annotated[
        CustomerAddressRepository, Depends(get_customer_address_repository)
    ],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    """List all addresses for the current customer."""
    use_case = ListAddressesUseCase(address_repository=address_repo)
    addresses = await use_case.execute(current_customer.id, skip=skip, limit=limit)

    return SuccessResponse(
        data=CustomerAddressListResponse(
            addresses=[
                CustomerAddressResponse(
                    id=addr.id,
                    customer_id=addr.customer_id,
                    first_name=addr.first_name,
                    last_name=addr.last_name,
                    full_name=addr.full_name,
                    address_line1=addr.address_line1,
                    address_line2=addr.address_line2,
                    city=addr.city,
                    state=addr.state,
                    postal_code=addr.postal_code,
                    country=addr.country,
                    phone=addr.phone,
                    is_default=addr.is_default,
                    label=addr.label,
                    formatted_address=addr.formatted_address,
                    created_at=str(addr.created_at) if addr.created_at else None,
                    updated_at=str(addr.updated_at) if addr.updated_at else None,
                )
                for addr in addresses
            ],
            total=len(addresses),
        ),
        message="Addresses retrieved successfully",
    )


@router.post(
    "/addresses",
    response_model=SuccessResponse[CustomerAddressResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create customer address",
)
async def create_customer_address(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    request: CreateAddressRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    address_repo: Annotated[
        CustomerAddressRepository, Depends(get_customer_address_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Create a new address for the current customer."""
    # Get store to get tenant_id
    store = await store_repo.get_by_id(current_customer.store_id)
    if not store:
        raise EntityNotFoundError("Store", str(current_customer.store_id))

    tenant_id = getattr(store, "tenant_id", store.id)

    use_case = CreateAddressUseCase(
        customer_repository=customer_repo,
        address_repository=address_repo,
    )

    dto = CreateAddressDTO(
        customer_id=str(current_customer.id),
        first_name=request.first_name,
        last_name=request.last_name,
        address_line1=request.address_line1,
        address_line2=request.address_line2,
        city=request.city,
        state=request.state,
        postal_code=request.postal_code,
        country=request.country,
        phone=request.phone,
        is_default=request.is_default,
        label=request.label,
    )

    result = await use_case.execute(current_customer.id, dto, tenant_id)

    return SuccessResponse(
        data=CustomerAddressResponse(
            id=result.id,
            customer_id=result.customer_id,
            first_name=result.first_name,
            last_name=result.last_name,
            full_name=result.full_name,
            address_line1=result.address_line1,
            address_line2=result.address_line2,
            city=result.city,
            state=result.state,
            postal_code=result.postal_code,
            country=result.country,
            phone=result.phone,
            is_default=result.is_default,
            label=result.label,
            formatted_address=result.formatted_address,
            created_at=str(result.created_at) if result.created_at else None,
            updated_at=str(result.updated_at) if result.updated_at else None,
        ),
        message="Address created successfully",
    )


@router.get(
    "/addresses/{address_id}",
    response_model=SuccessResponse[CustomerAddressResponse],
    summary="Get customer address",
)
async def get_customer_address(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    address_id: Annotated[UUID, Path(description="Address ID")],
    address_repo: Annotated[
        CustomerAddressRepository, Depends(get_customer_address_repository)
    ],
):
    """Get a specific address for the current customer."""
    address = await address_repo.get_by_id(address_id)

    if not address or address.customer_id != current_customer.id:
        raise EntityNotFoundError("Address", str(address_id))

    return SuccessResponse(
        data=CustomerAddressResponse(
            id=address.id,
            customer_id=address.customer_id,
            first_name=address.first_name,
            last_name=address.last_name,
            full_name=address.full_name,
            address_line1=address.address_line1,
            address_line2=address.address_line2,
            city=address.city,
            state=address.state,
            postal_code=address.postal_code,
            country=address.country,
            phone=address.phone,
            is_default=address.is_default,
            label=address.label,
            formatted_address=address.formatted_address,
            created_at=str(address.created_at) if address.created_at else None,
            updated_at=str(address.updated_at) if address.updated_at else None,
        ),
        message="Address retrieved successfully",
    )


@router.put(
    "/addresses/{address_id}",
    response_model=SuccessResponse[CustomerAddressResponse],
    summary="Update customer address",
)
async def update_customer_address(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    address_id: Annotated[UUID, Path(description="Address ID")],
    request: UpdateAddressRequest,
    address_repo: Annotated[
        CustomerAddressRepository, Depends(get_customer_address_repository)
    ],
):
    """Update an existing address for the current customer."""
    use_case = UpdateAddressUseCase(address_repository=address_repo)

    dto = UpdateAddressDTO(
        first_name=request.first_name,
        last_name=request.last_name,
        address_line1=request.address_line1,
        address_line2=request.address_line2,
        city=request.city,
        state=request.state,
        postal_code=request.postal_code,
        country=request.country,
        phone=request.phone,
        label=request.label,
    )

    result = await use_case.execute(current_customer.id, address_id, dto)

    return SuccessResponse(
        data=CustomerAddressResponse(
            id=result.id,
            customer_id=result.customer_id,
            first_name=result.first_name,
            last_name=result.last_name,
            full_name=result.full_name,
            address_line1=result.address_line1,
            address_line2=result.address_line2,
            city=result.city,
            state=result.state,
            postal_code=result.postal_code,
            country=result.country,
            phone=result.phone,
            is_default=result.is_default,
            label=result.label,
            formatted_address=result.formatted_address,
            created_at=str(result.created_at) if result.created_at else None,
            updated_at=str(result.updated_at) if result.updated_at else None,
        ),
        message="Address updated successfully",
    )


@router.delete(
    "/addresses/{address_id}",
    response_model=SuccessResponse[dict],
    summary="Delete customer address",
)
async def delete_customer_address(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    address_id: Annotated[UUID, Path(description="Address ID")],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    address_repo: Annotated[
        CustomerAddressRepository, Depends(get_customer_address_repository)
    ],
):
    """Delete an address for the current customer."""
    use_case = DeleteAddressUseCase(
        customer_repository=customer_repo,
        address_repository=address_repo,
    )

    await use_case.execute(current_customer.id, address_id)

    return SuccessResponse(
        data={"success": True},
        message="Address deleted successfully",
    )


@router.put(
    "/addresses/{address_id}/default",
    response_model=SuccessResponse[CustomerAddressResponse],
    summary="Set default address",
)
async def set_default_address(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    address_id: Annotated[UUID, Path(description="Address ID")],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    address_repo: Annotated[
        CustomerAddressRepository, Depends(get_customer_address_repository)
    ],
):
    """Set an address as the default for the current customer."""
    use_case = SetDefaultAddressUseCase(
        customer_repository=customer_repo,
        address_repository=address_repo,
    )

    result = await use_case.execute(current_customer.id, address_id)

    return SuccessResponse(
        data=CustomerAddressResponse(
            id=result.id,
            customer_id=result.customer_id,
            first_name=result.first_name,
            last_name=result.last_name,
            full_name=result.full_name,
            address_line1=result.address_line1,
            address_line2=result.address_line2,
            city=result.city,
            state=result.state,
            postal_code=result.postal_code,
            country=result.country,
            phone=result.phone,
            is_default=result.is_default,
            label=result.label,
            formatted_address=result.formatted_address,
            created_at=str(result.created_at) if result.created_at else None,
            updated_at=str(result.updated_at) if result.updated_at else None,
        ),
        message="Default address set successfully",
    )


# ============================================================================
# Notification Preference Routes
# ============================================================================

from pydantic import BaseModel as _BaseModel


class _NotificationChannelPrefs(_BaseModel):
    order_confirmation: bool | None = None
    shipping_update: bool | None = None
    delivery_confirmation: bool | None = None


class UpdateNotificationPrefsRequest(_BaseModel):
    email: _NotificationChannelPrefs | None = None
    whatsapp: _NotificationChannelPrefs | None = None


class NotificationPrefsResponse(_BaseModel):
    email: dict
    whatsapp: dict


@router.get(
    "/notification-preferences",
    response_model=SuccessResponse[NotificationPrefsResponse],
    summary="Get notification preferences",
)
async def get_notification_preferences(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
):
    """Get current customer notification preferences."""
    prefs = current_customer.notification_preferences
    return SuccessResponse(
        data=NotificationPrefsResponse(
            email=prefs.get("email", {}),
            whatsapp=prefs.get("whatsapp", {}),
        ),
        message="Notification preferences retrieved successfully",
    )


@router.put(
    "/notification-preferences",
    response_model=SuccessResponse[NotificationPrefsResponse],
    summary="Update notification preferences",
)
async def update_notification_preferences(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    request: UpdateNotificationPrefsRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Update customer notification preferences (opt-in / opt-out)."""
    update_dict: dict = {}
    if request.email:
        update_dict["email"] = dict(request.email.model_dump(exclude_none=True).items())
    if request.whatsapp:
        update_dict["whatsapp"] = dict(
            request.whatsapp.model_dump(exclude_none=True).items()
        )

    current_customer.update_notification_preferences(update_dict)
    await customer_repo.update(current_customer)

    prefs = current_customer.notification_preferences
    return SuccessResponse(
        data=NotificationPrefsResponse(
            email=prefs.get("email", {}),
            whatsapp=prefs.get("whatsapp", {}),
        ),
        message="Notification preferences updated successfully",
    )


# ============================================================================
# Order History Routes
# ============================================================================

from src.api.v1.schemas.tenant.common import PaginatedListResponse
from src.api.v1.schemas.tenant.order import (
    OrderAddressResponse,
    OrderLineItemResponse,
    OrderListItemResponse,
    OrderResponse,
)
from src.application.dto.order import OrderDTO


def _order_address_to_response(address_dto) -> OrderAddressResponse:
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
    return OrderResponse(
        id=str(order_dto.id),
        store_id=str(order_dto.store_id),
        customer_id=str(order_dto.customer_id),
        order_number=order_dto.order_number,
        line_items=[_order_line_item_to_response(i) for i in order_dto.line_items],
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


@router.get(
    "/orders",
    response_model=SuccessResponse[PaginatedListResponse[OrderListItemResponse]],
    summary="List customer orders",
)
async def list_customer_orders(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List all orders placed by the current customer."""
    skip = (page - 1) * limit
    orders = await order_repo.get_by_customer(
        customer_id=current_customer.id,
        skip=skip,
        limit=limit,
    )
    total = await order_repo.count_by_customer(current_customer.id)

    items = [
        OrderListItemResponse(
            id=str(o.id),
            order_number=o.order_number,
            customer_id=str(o.customer_id),
            customer_name=f"{current_customer.first_name} {current_customer.last_name}",
            status=o.status.value,
            payment_status=o.payment_status.value,
            fulfillment_status=o.fulfillment_status.value,
            total=o.total,
            currency=o.currency,
            item_count=o.item_count,
            payment_method=o.payment_method,
            created_at=str(o.created_at),
        )
        for o in orders
    ]

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
    "/orders/{order_id}",
    response_model=SuccessResponse[OrderResponse],
    summary="Get customer order detail",
)
async def get_customer_order(
    order_id: Annotated[UUID, Path(description="Order ID")],
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
):
    """Get detailed view of a specific order belonging to the current customer."""
    order = await order_repo.get_by_id(order_id)

    if not order or order.customer_id != current_customer.id:
        raise EntityNotFoundError("Order", str(order_id))

    order_dto = OrderDTO.from_entity(order)

    return SuccessResponse(
        data=_order_to_response(order_dto),
        message="Order retrieved successfully",
    )

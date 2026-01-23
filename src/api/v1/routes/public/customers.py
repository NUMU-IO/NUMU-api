"""Customer storefront routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from src.api.dependencies import (
    get_customer_address_repository,
    get_customer_repository,
    get_password_service,
    get_store_repository,
    get_token_service,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.customer import (
    CreateAddressRequest,
    CustomerAddressListResponse,
    CustomerAddressResponse,
    CustomerAuthResponse,
    CustomerChangePasswordRequest,
    CustomerLoginRequest,
    CustomerRegisterRequest,
    CustomerResponse,
    CustomerUpdateProfileRequest,
    UpdateAddressRequest,
)
from src.application.dto.customer import (
    CreateAddressDTO,
    CustomerChangePasswordDTO,
    CustomerLoginDTO,
    CustomerRegisterDTO,
    CustomerUpdateProfileDTO,
    UpdateAddressDTO,
)
from src.application.use_cases.customers import (
    ChangeCustomerPasswordUseCase,
    GetCustomerProfileUseCase,
    LoginCustomerUseCase,
    RegisterCustomerUseCase,
    UpdateCustomerProfileUseCase,
)
from src.application.use_cases.customers.addresses import (
    CreateAddressUseCase,
    DeleteAddressUseCase,
    ListAddressesUseCase,
    SetDefaultAddressUseCase,
    UpdateAddressUseCase,
)
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.external_services import PasswordService, TokenService
from src.infrastructure.repositories import (
    CustomerAddressRepository,
    CustomerRepository,
    StoreRepository,
)

router = APIRouter(prefix="/store/{store_id}/customers", tags=["Customer Storefront"])


# ============== Authentication Routes ==============


@router.post(
    "/register",
    response_model=SuccessResponse[CustomerAuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register new customer",
)
async def register_customer(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CustomerRegisterRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Register a new customer for the store."""
    # Verify store exists and get tenant_id
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    use_case = RegisterCustomerUseCase(
        customer_repository=customer_repo,
        password_service=password_service,
        token_service=token_service,
    )

    dto = CustomerRegisterDTO(
        store_id=str(store_id),
        email=request.email,
        password=request.password,
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
        accepts_marketing=request.accepts_marketing,
    )

    # Get tenant_id from store (assuming store has tenant_id attribute)
    tenant_id = getattr(store, "tenant_id", store_id)  # Fallback to store_id if no tenant_id
    result = await use_case.execute(dto, tenant_id)

    return SuccessResponse(
        data=CustomerAuthResponse(
            customer=CustomerResponse(
                id=result.customer.id,
                store_id=result.customer.store_id,
                email=result.customer.email,
                first_name=result.customer.first_name,
                last_name=result.customer.last_name,
                full_name=result.customer.full_name,
                phone=result.customer.phone,
                accepts_marketing=result.customer.accepts_marketing,
                is_verified=result.customer.is_verified,
                total_orders=result.customer.total_orders,
                total_spent=result.customer.total_spent,
                default_address_id=result.customer.default_address_id,
                created_at=str(result.customer.created_at) if result.customer.created_at else None,
                updated_at=str(result.customer.updated_at) if result.customer.updated_at else None,
            ),
            tokens={
                "access_token": result.tokens.access_token,
                "refresh_token": result.tokens.refresh_token,
                "token_type": result.tokens.token_type,
            },
        ),
        message="Customer registered successfully",
    )


@router.post(
    "/login",
    response_model=SuccessResponse[CustomerAuthResponse],
    summary="Login customer",
)
async def login_customer(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CustomerLoginRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Authenticate customer and return tokens."""
    use_case = LoginCustomerUseCase(
        customer_repository=customer_repo,
        password_service=password_service,
        token_service=token_service,
    )

    dto = CustomerLoginDTO(
        store_id=str(store_id),
        email=request.email,
        password=request.password,
    )

    result = await use_case.execute(dto)

    return SuccessResponse(
        data=CustomerAuthResponse(
            customer=CustomerResponse(
                id=result.customer.id,
                store_id=result.customer.store_id,
                email=result.customer.email,
                first_name=result.customer.first_name,
                last_name=result.customer.last_name,
                full_name=result.customer.full_name,
                phone=result.customer.phone,
                accepts_marketing=result.customer.accepts_marketing,
                is_verified=result.customer.is_verified,
                total_orders=result.customer.total_orders,
                total_spent=result.customer.total_spent,
                default_address_id=result.customer.default_address_id,
                created_at=str(result.customer.created_at) if result.customer.created_at else None,
                updated_at=str(result.customer.updated_at) if result.customer.updated_at else None,
            ),
            tokens={
                "access_token": result.tokens.access_token,
                "refresh_token": result.tokens.refresh_token,
                "token_type": result.tokens.token_type,
            },
        ),
        message="Login successful",
    )


# ============== Profile Routes ==============
# Note: These routes would need customer authentication middleware
# For now, using customer_id as path parameter for demonstration


@router.get(
    "/{customer_id}/profile",
    response_model=SuccessResponse[CustomerResponse],
    summary="Get customer profile",
)
async def get_customer_profile(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Get customer profile by ID."""
    use_case = GetCustomerProfileUseCase(customer_repository=customer_repo)
    result = await use_case.execute(customer_id)

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
    "/{customer_id}/profile",
    response_model=SuccessResponse[CustomerResponse],
    summary="Update customer profile",
)
async def update_customer_profile(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    request: CustomerUpdateProfileRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Update customer profile."""
    use_case = UpdateCustomerProfileUseCase(customer_repository=customer_repo)

    dto = CustomerUpdateProfileDTO(
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
        accepts_marketing=request.accepts_marketing,
    )

    result = await use_case.execute(customer_id, dto)

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
    "/{customer_id}/password",
    response_model=SuccessResponse[dict],
    summary="Change customer password",
)
async def change_customer_password(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    request: CustomerChangePasswordRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
):
    """Change customer password."""
    use_case = ChangeCustomerPasswordUseCase(
        customer_repository=customer_repo,
        password_service=password_service,
    )

    dto = CustomerChangePasswordDTO(
        current_password=request.current_password,
        new_password=request.new_password,
    )

    await use_case.execute(customer_id, dto)

    return SuccessResponse(
        data={"success": True},
        message="Password changed successfully",
    )


# ============== Address Routes ==============


@router.get(
    "/{customer_id}/addresses",
    response_model=SuccessResponse[CustomerAddressListResponse],
    summary="List customer addresses",
)
async def list_customer_addresses(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    address_repo: Annotated[CustomerAddressRepository, Depends(get_customer_address_repository)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    """List all addresses for a customer."""
    use_case = ListAddressesUseCase(address_repository=address_repo)
    addresses = await use_case.execute(customer_id, skip=skip, limit=limit)

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
    "/{customer_id}/addresses",
    response_model=SuccessResponse[CustomerAddressResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create customer address",
)
async def create_customer_address(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    request: CreateAddressRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    address_repo: Annotated[CustomerAddressRepository, Depends(get_customer_address_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Create a new address for a customer."""
    # Get tenant_id from customer
    customer = await customer_repo.get_by_id(customer_id)
    if not customer:
        raise EntityNotFoundError("Customer", str(customer_id))

    # Get store to get tenant_id
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    tenant_id = getattr(store, "tenant_id", store_id)

    use_case = CreateAddressUseCase(
        customer_repository=customer_repo,
        address_repository=address_repo,
    )

    dto = CreateAddressDTO(
        customer_id=str(customer_id),
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

    result = await use_case.execute(customer_id, dto, tenant_id)

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


@router.put(
    "/{customer_id}/addresses/{address_id}",
    response_model=SuccessResponse[CustomerAddressResponse],
    summary="Update customer address",
)
async def update_customer_address(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    address_id: Annotated[UUID, Path(description="Address ID")],
    request: UpdateAddressRequest,
    address_repo: Annotated[CustomerAddressRepository, Depends(get_customer_address_repository)],
):
    """Update an existing address."""
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

    result = await use_case.execute(customer_id, address_id, dto)

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
    "/{customer_id}/addresses/{address_id}",
    response_model=SuccessResponse[dict],
    summary="Delete customer address",
)
async def delete_customer_address(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    address_id: Annotated[UUID, Path(description="Address ID")],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    address_repo: Annotated[CustomerAddressRepository, Depends(get_customer_address_repository)],
):
    """Delete an address."""
    use_case = DeleteAddressUseCase(
        customer_repository=customer_repo,
        address_repository=address_repo,
    )

    await use_case.execute(customer_id, address_id)

    return SuccessResponse(
        data={"success": True},
        message="Address deleted successfully",
    )


@router.put(
    "/{customer_id}/addresses/{address_id}/default",
    response_model=SuccessResponse[CustomerAddressResponse],
    summary="Set default address",
)
async def set_default_address(
    store_id: Annotated[UUID, Path(description="Store ID")],
    customer_id: Annotated[UUID, Path(description="Customer ID")],
    address_id: Annotated[UUID, Path(description="Address ID")],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    address_repo: Annotated[CustomerAddressRepository, Depends(get_customer_address_repository)],
):
    """Set an address as the default."""
    use_case = SetDefaultAddressUseCase(
        customer_repository=customer_repo,
        address_repository=address_repo,
    )

    result = await use_case.execute(customer_id, address_id)

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

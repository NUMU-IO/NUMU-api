"""Public storefront routes.

URL: /storefront/store/{store_id}/...

These routes are publicly accessible without authentication:
- Product catalog
- Category listing
- Customer registration and login
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from src.api.dependencies import (
    get_customer_repository,
    get_password_service,
    get_product_repository,
    get_store_repository,
    get_token_service,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas import PaginatedListResponse, ProductResponse
from src.api.v1.schemas.public.customer import (
    CustomerAuthResponse,
    CustomerLoginRequest,
    CustomerRegisterRequest,
    CustomerResponse,
)
from src.application.dto.customer import (
    CustomerLoginDTO,
    CustomerRegisterDTO,
)
from src.application.use_cases.customers import (
    LoginCustomerUseCase,
    RegisterCustomerUseCase,
)
from src.application.use_cases.products import ListProductsUseCase
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.external_services import PasswordService, TokenService
from src.infrastructure.repositories import (
    CustomerRepository,
    ProductRepository,
    StoreRepository,
)

router = APIRouter()


# ============================================================================
# Public Catalog Routes
# ============================================================================


@router.get(
    "/products",
    response_model=SuccessResponse[PaginatedListResponse[ProductResponse]],
    summary="Browse store products",
)
async def browse_products(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    category_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
):
    """Browse products in a store's catalog (public)."""
    # Verify store exists
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    use_case = ListProductsUseCase(product_repository=product_repo)

    if search:
        result = await use_case.search(
            store_id=store_id,
            query=search,
            page=page,
            page_size=limit,
        )
    elif category_id:
        result = await use_case.by_category(
            category_id=category_id,
            page=page,
            page_size=limit,
        )
    else:
        # Only show active products in storefront
        result = await use_case.execute(
            store_id=store_id,
            page=page,
            page_size=limit,
            status="active",
        )

    products = [
        ProductResponse(
            id=str(product.id),
            store_id=str(product.store_id),
            name=product.name,
            slug=product.slug,
            description=product.description,
            short_description=product.short_description,
            product_type=product.product_type,
            status=product.status,
            price=str(product.price),
            price_currency=product.price_currency,
            compare_at_price=str(product.compare_at_price) if product.compare_at_price else None,
            cost_price=None,  # Don't expose cost price in storefront
            sku=product.sku,
            quantity=product.quantity,
            is_in_stock=product.is_in_stock,
            is_low_stock=product.is_low_stock,
            is_on_sale=product.is_on_sale,
            category_id=str(product.category_id) if product.category_id else None,
            images=product.images,
            tags=product.tags,
            attributes=product.attributes,
            created_at=str(product.created_at),
            updated_at=str(product.updated_at),
        )
        for product in result.items
    ]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=products,
            total=result.total,
            page=page,
            page_size=limit,
            total_pages=(result.total + limit - 1) // limit if limit > 0 else 0,
        ),
        message="Products retrieved successfully",
    )


@router.get(
    "/products/{product_slug}",
    response_model=SuccessResponse[ProductResponse],
    summary="Get product by slug",
)
async def get_product_by_slug(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_slug: Annotated[str, Path(description="Product slug")],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Get a product by its slug (public)."""
    # Verify store exists
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    product = await product_repo.get_by_slug(store_id, product_slug)

    if not product:
        raise EntityNotFoundError("Product", product_slug)

    return SuccessResponse(
        data=ProductResponse(
            id=str(product.id),
            store_id=str(product.store_id),
            name=product.name,
            slug=product.slug,
            description=product.description,
            short_description=product.short_description,
            product_type=product.product_type,
            status=product.status,
            price=str(product.price),
            price_currency=product.price_currency,
            compare_at_price=str(product.compare_at_price) if product.compare_at_price else None,
            cost_price=None,  # Don't expose cost price in storefront
            sku=product.sku,
            quantity=product.quantity,
            is_in_stock=product.is_in_stock,
            is_low_stock=product.is_low_stock,
            is_on_sale=product.is_on_sale,
            category_id=str(product.category_id) if product.category_id else None,
            images=product.images,
            tags=product.tags,
            attributes=product.attributes,
            created_at=str(product.created_at),
            updated_at=str(product.updated_at),
        ),
        message="Product retrieved successfully",
    )


# ============================================================================
# Customer Authentication Routes
# ============================================================================


@router.post(
    "/auth/register",
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

    # Get tenant_id from store
    tenant_id = getattr(store, "tenant_id", store.id)
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
    "/auth/login",
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

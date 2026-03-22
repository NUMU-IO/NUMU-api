"""Public storefront routes.

URL: /storefront/store/{store_id}/...

These routes are publicly accessible without authentication:
- Product catalog
- Category listing
- Customer registration and login
"""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)

from src.api.dependencies import (
    CursorParams,
    FieldSelector,
    build_cursor_response,
    get_category_repository,
    get_cursor_params,
    get_cursor_values,
    get_customer_repository,
    get_password_service,
    get_product_cache_service,
    get_product_field_selector,
    get_product_repository,
    get_store_repository,
    get_token_service,
)
from src.api.responses import SuccessResponse
from src.api.utils.cookies import (
    clear_customer_auth_cookies,
    set_customer_auth_cookies,
)
from src.api.v1.routes.storefront.theme_schemas import get_theme_schema
from src.api.v1.schemas import (
    CursorPaginatedListResponse,
    PaginatedListResponse,
    ProductResponse,
)
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
from src.core.entities.store import StoreStatus
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.cache import ProductCacheService
from src.infrastructure.external_services import PasswordService, TokenService
from src.infrastructure.repositories import (
    CategoryRepository,
    CustomerRepository,
    ProductRepository,
    StoreRepository,
)

router = APIRouter()


def _customer_response(c) -> CustomerResponse:
    """Build a CustomerResponse, converting value objects to str."""
    return CustomerResponse(
        id=str(c.id),
        store_id=str(c.store_id),
        email=c.email.value if hasattr(c.email, "value") else str(c.email),
        first_name=c.first_name,
        last_name=c.last_name,
        full_name=c.full_name,
        phone=c.phone.value
        if c.phone and hasattr(c.phone, "value")
        else (str(c.phone) if c.phone else None),
        accepts_marketing=c.accepts_marketing,
        is_verified=c.is_verified,
        total_orders=c.total_orders,
        total_spent=c.total_spent,
        default_address_id=str(c.default_address_id) if c.default_address_id else None,
        created_at=str(c.created_at) if c.created_at else None,
        updated_at=str(c.updated_at) if c.updated_at else None,
    )


# Router for routes that don't require a store_id path param
lookup_router = APIRouter()


# ============================================================================
# Available Themes (public, no auth needed)
# ============================================================================

# Static theme list matching numu-egyptian-bazaar's theme engine.
# Kept in code so the dashboard can discover available themes without
# bundling storefront source.
AVAILABLE_THEMES = [
    {
        "id": "modern",
        "name": "Modern Minimal",
        "nameAr": "مودرن",
        "layout": "default",
        "description": "Clean, contemporary design with teal accents",
    },
    {
        "id": "boutique",
        "name": "Boutique Chic",
        "nameAr": "بوتيك",
        "layout": "default",
        "description": "Warm pink/magenta tones for fashion-forward stores",
    },
    {
        "id": "elegant",
        "name": "Elegant Luxury",
        "nameAr": "أنيق",
        "layout": "default",
        "description": "Rich brown and gold palette for premium brands",
    },
    {
        "id": "skeuomorphic",
        "name": "Skeuomorphic",
        "nameAr": "واقعي",
        "layout": "skeuomorphic",
        "description": "Textured 3D design with depth and shadow effects",
    },
    {
        "id": "tech-wave",
        "name": "Tech Wave",
        "nameAr": "موجة تقنية",
        "layout": "default",
        "description": "Futuristic dark theme with neon accents, glassmorphism, and wave effects",
    },
    {
        "id": "neo-brutalism",
        "name": "Neo Brutalism",
        "nameAr": "نيو بروتاليزم",
        "layout": "neo-brutalism",
        "description": "Bold, raw design with thick borders, hard shadows, and neon accents",
    },
    {
        "id": "editorial",
        "name": "Editorial",
        "nameAr": "إيديتوريال",
        "layout": "editorial",
        "description": "Bold editorial fashion theme with oversized typography and dramatic green palette",
    },
    {
        "id": "luxury-minimal",
        "name": "Luxury Minimal",
        "nameAr": "فخامة مينيمال",
        "layout": "luxury-minimal",
        "description": "Ultra-clean luxury minimalist theme with refined typography and understated elegance",
    },
]


@lookup_router.get(
    "/themes",
    response_model=SuccessResponse,
    summary="List available storefront themes",
)
async def list_themes():
    """Return the list of available storefront themes for the dashboard."""
    return SuccessResponse(
        data=AVAILABLE_THEMES,
        message="Themes retrieved successfully",
    )


@lookup_router.get(
    "/themes/{theme_id}/schemas",
    response_model=SuccessResponse,
    summary="Get theme schemas (global settings + section schemas)",
    operation_id="get_theme_schemas",
)
async def get_theme_schemas(
    theme_id: Annotated[str, Path(description="Theme ID (e.g., modern, skeuomorphic)")],
):
    """Return the full schema bundle for a theme.

    The dashboard uses these schemas to dynamically generate the editor UI.
    Returns global settings, all section schemas, and default templates.
    """
    schema_bundle = get_theme_schema(theme_id)
    if schema_bundle is None:
        raise EntityNotFoundError("Theme", theme_id)

    return SuccessResponse(
        data=schema_bundle,
        message="Theme schemas retrieved successfully",
    )


# ============================================================================
# Store Lookup by Subdomain
# ============================================================================


@lookup_router.get(
    "/store-by-subdomain/{subdomain}",
    response_model=SuccessResponse,
    summary="Get store info by subdomain",
    operation_id="get_store_by_subdomain",
)
async def get_store_by_subdomain(
    subdomain: Annotated[str, Path(description="Store subdomain")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Look up a store by its subdomain (public). Used by the storefront frontend."""
    store = await store_repo.get_by_subdomain(subdomain.lower())
    if not store:
        raise EntityNotFoundError("Store", subdomain)

    # Pending stores are not public yet
    if store.status == StoreStatus.PENDING_APPROVAL:
        raise EntityNotFoundError("Store", subdomain)

    return SuccessResponse(
        data={
            "id": str(store.id),
            "name": store.name,
            "slug": store.slug,
            "subdomain": store.subdomain,
            "description": store.description,
            "logo_url": store.logo_url,
            "banner_url": store.banner_url,
            "status": store.status.value
            if hasattr(store.status, "value")
            else str(store.status),
            "settings": store.settings or {},
            "theme_settings": store.theme_settings,
            "default_currency": store.default_currency.value
            if hasattr(store.default_currency, "value")
            else str(store.default_currency),
            "default_language": store.default_language,
            "social_links": store.social_links,
        },
        message="Store retrieved successfully",
    )


# ============================================================================
# Public Catalog Routes
# ============================================================================


@router.get(
    "/products",
    response_model=SuccessResponse[PaginatedListResponse[ProductResponse]],
    summary="Browse store products",
    operation_id="browse_products",
)
async def browse_products(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_cache: Annotated[ProductCacheService, Depends(get_product_cache_service)],
    field_selector: Annotated[FieldSelector, Depends(get_product_field_selector)],
    category_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    fields: str | None = Query(
        None,
        description="Comma-separated list of fields to include (e.g., id,name,price,images). "
        "Omit for mobile-optimized default fields.",
        example="id,name,slug,price,images",
        max_length=500,
    ),
):
    """Browse products in a store's catalog (public).

    Uses Redis caching with 5-minute TTL for improved 3G performance.
    Search queries bypass cache for real-time results.
    Supports sparse fieldsets for 3G optimization via ?fields= parameter.
    Default fields are optimized for mobile list views.
    """
    # Verify store exists
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    # Skip cache for search queries (dynamic, less cacheable)
    use_cache = search is None

    # Try cache first (cache-aside pattern)
    if use_cache:
        cached_data = await product_cache.get_products(
            store_id=store_id,
            category_id=category_id,
            page=page,
            limit=limit,
        )
        if cached_data:
            # Cache hit - return directly
            return SuccessResponse(
                data=PaginatedListResponse(**cached_data),
                message="Products retrieved successfully",
            )

    # Cache miss or search query - fetch from database
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
        skip = 0 if page < 1 else (page - 1) * limit
        result = await use_case.execute(
            store_id=store_id,
            skip=skip,
            limit=limit,
            is_active=True,
        )

    # Build product responses
    products = []
    for product in result.items:
        product_data = {
            "id": str(product.id),
            "store_id": str(product.store_id),
            "name": product.name,
            "slug": product.slug,
            "description": product.description,
            "short_description": product.short_description,
            "product_type": product.product_type,
            "status": product.status,
            "price": str(product.price),
            "price_currency": product.price_currency,
            "compare_at_price": str(product.compare_at_price)
            if product.compare_at_price
            else None,
            "cost_price": None,  # Never expose cost price in storefront
            "sku": product.sku,
            "quantity": product.quantity,
            "is_in_stock": product.is_in_stock,
            "is_low_stock": product.is_low_stock,
            "is_on_sale": product.is_on_sale,
            "category_id": str(product.category_id) if product.category_id else None,
            "images": product.images,
            "tags": product.tags,
            "attributes": product.attributes,
            "created_at": str(product.created_at),
            "updated_at": str(product.updated_at),
        }

        # Apply sparse fieldsets only when client explicitly requests specific fields
        if fields:
            requested_fields = field_selector.parse_fields(fields)
            product_data = field_selector.filter_dict(product_data, requested_fields)
        products.append(product_data)

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
    "/products/cursor",
    response_model=SuccessResponse[CursorPaginatedListResponse[ProductResponse]],
    summary="Browse products with cursor pagination (3G optimized)",
    operation_id="browse_products_cursor",
)
async def browse_products_cursor(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    cursor_params: Annotated[CursorParams, Depends(get_cursor_params)],
    category_id: UUID | None = Query(None, description="Filter by category"),
):
    """Browse products with cursor-based pagination.

    Optimized for mobile/3G networks:
    - O(1) performance regardless of page depth
    - No expensive COUNT queries (no total)
    - Opaque cursor tokens for stable pagination

    Recommended page sizes:
    - 3G: 10-15 items (default: 15)
    - 4G: 20-30 items
    - WiFi: 30-50 items
    """
    # Verify store exists
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    # Get cursor values if provided
    cursor_data = get_cursor_values(cursor_params.cursor)

    # Fetch products with cursor pagination
    # Request one extra item to determine has_more
    fetch_limit = cursor_params.limit + 1

    # Build query with cursor filter
    if cursor_data:
        cursor_ts, cursor_id = cursor_data
        # Fetch products created before the cursor (descending order)
        result = await product_repo.list_with_cursor(
            store_id=store_id,
            category_id=category_id,
            cursor_timestamp=cursor_ts,
            cursor_id=cursor_id,
            limit=fetch_limit,
            is_active=True,
        )
    else:
        # First page - no cursor
        result = await product_repo.list_with_cursor(
            store_id=store_id,
            category_id=category_id,
            cursor_timestamp=None,
            cursor_id=None,
            limit=fetch_limit,
            is_active=True,
        )

    # Build cursor pagination metadata
    pagination = build_cursor_response(
        items=result,
        limit=cursor_params.limit,
        id_field="id",
        timestamp_field="created_at",
    )

    # Trim to requested limit
    items = result[: cursor_params.limit]

    # Build product responses
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
            compare_at_price=str(product.compare_at_price)
            if product.compare_at_price
            else None,
            cost_price=None,  # Never expose in storefront
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
        for product in items
    ]

    return SuccessResponse(
        data=CursorPaginatedListResponse(
            items=products,
            next_cursor=pagination["next_cursor"],
            prev_cursor=pagination["prev_cursor"],
            has_more=pagination["has_more"],
        ),
        message="Products retrieved successfully",
    )


@router.get(
    "/products/{product_slug}",
    response_model=SuccessResponse[ProductResponse],
    summary="Get product by slug",
    operation_id="get_product_by_slug",
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
            compare_at_price=str(product.compare_at_price)
            if product.compare_at_price
            else None,
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
# Category Routes (public)
# ============================================================================


@router.get(
    "/categories",
    response_model=SuccessResponse[list],
    summary="List store categories",
    operation_id="browse_categories",
)
async def browse_categories(
    store_id: Annotated[UUID, Path(description="Store ID")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
):
    """List active categories for a store (public)."""
    from src.application.use_cases.categories import ListCategoriesUseCase

    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    use_case = ListCategoriesUseCase(category_repository=category_repo)
    results = await use_case.execute(store_id=store_id, include_inactive=False)

    return SuccessResponse(
        data=[
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "description": r.description,
                "image_url": r.image_url,
                "parent_id": str(r.parent_id) if r.parent_id else None,
                "position": r.position,
                "product_count": r.product_count,
            }
            for r in results
        ],
        message="Categories retrieved successfully",
    )


# ============================================================================
# Customer Authentication Routes
# ============================================================================


@router.post(
    "/auth/register",
    response_model=SuccessResponse[CustomerAuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register new customer",
    operation_id="register_customer",
)
async def register_customer(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CustomerRegisterRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    response: Response,
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

    # Set customer auth cookies
    set_customer_auth_cookies(
        response, result.tokens.access_token, result.tokens.refresh_token
    )

    # Send verification email in the background (non-blocking)
    try:
        import random

        from src.infrastructure.cache.redis_cache import RedisCacheService
        from src.infrastructure.external_services.resend.email_service import (
            ResendEmailService,
        )

        code = f"{random.randint(0, 999999):06d}"
        cache = RedisCacheService()
        await cache.set(
            f"customer_email_verify_code:{result.customer.id}",
            code,
            expire=86400,
        )
        email_service = ResendEmailService()
        await email_service.send_verification_email(
            email=request.email,
            token="",
            code=code,
        )
    except Exception:
        pass  # Non-critical — customer can request resend later

    return SuccessResponse(
        data=CustomerAuthResponse(customer=_customer_response(result.customer)),
        message="Customer registered successfully",
    )


@router.post(
    "/auth/login",
    response_model=SuccessResponse[CustomerAuthResponse],
    summary="Login customer",
    operation_id="login_customer",
)
async def login_customer(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CustomerLoginRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    response: Response,
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

    # Set customer auth cookies
    set_customer_auth_cookies(
        response, result.tokens.access_token, result.tokens.refresh_token
    )

    return SuccessResponse(
        data=CustomerAuthResponse(customer=_customer_response(result.customer)),
        message="Login successful",
    )


# ============================================================================
# Customer Token Refresh
# ============================================================================


@router.post(
    "/auth/refresh",
    response_model=SuccessResponse,
    summary="Refresh customer access token",
    operation_id="refresh_customer_token",
)
async def refresh_customer_token(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: Request,
    response: Response,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Refresh customer access token using the customer_refresh_token cookie."""
    refresh_tok = request.cookies.get("customer_refresh_token")
    if not refresh_tok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found",
        )

    from src.core.exceptions import InvalidTokenError, TokenExpiredError

    try:
        payload = token_service.verify_customer_token(refresh_tok)
    except TokenExpiredError:
        clear_customer_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
        )
    except InvalidTokenError:
        clear_customer_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if payload.token_type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    if payload.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not belong to this store",
        )

    customer = await customer_repo.get_by_id(payload.customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Customer not found",
        )

    new_access = token_service.create_customer_access_token(customer)
    new_refresh = token_service.create_customer_refresh_token(customer)
    set_customer_auth_cookies(response, new_access, new_refresh)

    return SuccessResponse(
        data={"message": "Token refreshed"},
        message="Token refreshed successfully",
    )


# ============================================================================
# Customer Logout
# ============================================================================


@router.post(
    "/auth/logout",
    response_model=SuccessResponse,
    summary="Logout customer",
    operation_id="logout_customer",
)
async def logout_customer(
    store_id: Annotated[UUID, Path(description="Store ID")],
    response: Response,
):
    """Clear customer auth cookies to log them out."""
    clear_customer_auth_cookies(response)
    return SuccessResponse(
        data={"message": "Logged out successfully"},
        message="Logged out successfully",
    )


# ============================================================================
# Customer Email Verification
# ============================================================================

from pydantic import BaseModel as _BaseModel
from pydantic import Field as _Field

from src.api.dependencies.auth import get_current_customer_payload
from src.core.interfaces.services.token_service import CustomerTokenPayload


class _VerifyEmailCodeRequest(_BaseModel):
    code: str = _Field(
        ..., min_length=6, max_length=6, description="6-digit verification code"
    )


@router.post(
    "/auth/verify-email",
    response_model=SuccessResponse,
    summary="Verify customer email with code",
    operation_id="verify_customer_email",
)
async def verify_customer_email(
    store_id: Annotated[UUID, Path(description="Store ID")],
    body: _VerifyEmailCodeRequest,
    payload: Annotated[CustomerTokenPayload, Depends(get_current_customer_payload)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Verify a customer's email address using the 6-digit code."""
    from src.infrastructure.cache.redis_cache import RedisCacheService

    cache = RedisCacheService()
    cache_key = f"customer_email_verify_code:{payload.customer_id}"
    stored_code = await cache.get(cache_key)

    if not stored_code or stored_code != body.code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    customer = await customer_repo.get_by_id(payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not customer.is_verified:
        customer.verify()
        await customer_repo.update(customer)

    await cache.delete(cache_key)

    return SuccessResponse(
        data={"message": "Email verified successfully"},
        message="Email verified successfully",
    )


@router.post(
    "/auth/resend-verification",
    response_model=SuccessResponse,
    summary="Resend customer verification email",
    operation_id="resend_customer_verification",
)
async def resend_customer_verification(
    store_id: Annotated[UUID, Path(description="Store ID")],
    payload: Annotated[CustomerTokenPayload, Depends(get_current_customer_payload)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Resend verification email with a new 6-digit code."""
    import random

    from src.infrastructure.cache.redis_cache import RedisCacheService
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    customer = await customer_repo.get_by_id(payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if customer.is_verified:
        return SuccessResponse(
            data={"message": "Email is already verified"},
            message="Email is already verified",
        )

    code = f"{random.randint(0, 999999):06d}"
    cache = RedisCacheService()
    await cache.set(
        f"customer_email_verify_code:{customer.id}",
        code,
        expire=86400,
    )

    email_addr = (
        customer.email.value
        if hasattr(customer.email, "value")
        else str(customer.email)
    )

    try:
        email_service = ResendEmailService()
        await email_service.send_verification_email(
            email=email_addr,
            token="",  # no link-based verification for customers
            code=code,
        )
    except Exception:
        pass  # Non-critical — code is stored; customer can retry

    return SuccessResponse(
        data={"message": "Verification email sent"},
        message="Verification email sent",
    )


# ============================================================================
# Payment Methods
# ============================================================================


@router.get(
    "/payment-methods",
    response_model=SuccessResponse[dict],
    summary="Get available payment methods for this store",
    operation_id="get_store_payment_methods",
)
async def get_store_payment_methods(
    store_id: UUID = Path(...),
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)] = ...,
):
    """Return enabled + configured payment methods for the storefront."""
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")

    payment_settings = (store.settings or {}).get("payment", {})

    methods = []
    if payment_settings.get("cod", {}).get("enabled"):
        methods.append({
            "id": "cod",
            "label": "الدفع عند الاستلام",
            "label_en": "Cash on Delivery",
            "type": "cod",
        })
    if payment_settings.get("paymob", {}).get("enabled") and payment_settings.get(
        "paymob", {}
    ).get("is_configured"):
        methods.append({
            "id": "paymob_card",
            "label": "بطاقة بنكية",
            "label_en": "Credit/Debit Card",
            "type": "paymob",
        })
        # Add wallet if configured
        if payment_settings.get("paymob", {}).get("encrypted_credentials"):
            methods.append({
                "id": "paymob_wallet",
                "label": "محفظة إلكترونية",
                "label_en": "Mobile Wallet",
                "type": "paymob",
            })
    if payment_settings.get("fawry", {}).get("enabled") and payment_settings.get(
        "fawry", {}
    ).get("is_configured"):
        methods.append({
            "id": "fawry",
            "label": "فوري",
            "label_en": "Fawry",
            "type": "fawry",
        })

    # Kashier uses tenant credential system — check if configured
    if payment_settings.get("kashier", {}).get("enabled") and payment_settings.get(
        "kashier", {}
    ).get("is_configured"):
        methods.append({
            "id": "kashier",
            "label": "بطاقة بنكية",
            "label_en": "Credit/Debit Card",
            "type": "kashier",
        })

    return SuccessResponse(
        data={"methods": methods},
        message="Payment methods retrieved",
    )

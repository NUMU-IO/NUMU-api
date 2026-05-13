"""Public storefront routes.

URL: /storefront/store/{store_id}/...

These routes are publicly accessible without authentication:
- Product catalog
- Category listing
- Customer registration and login
"""

from typing import Annotated, Any
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
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

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
    get_storefront_cache_service,
    get_token_service,
)
from src.api.dependencies.database import get_db
from src.api.dependencies.feature_flags import _read_feature_flags
from src.api.dependencies.repositories import get_product_subscription_repository
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
from src.infrastructure.cache import (
    MISSING_SENTINEL,
    ProductCacheService,
    StorefrontCache,
)
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
    {
        "id": "empire",
        "name": "Empire",
        "nameAr": "إمباير",
        "layout": "empire",
        "description": "Premium editorial e-commerce with monochromatic palette and content-first design",
    },
    {
        "id": "kick-game",
        "name": "Kick Game",
        "nameAr": "كيك جيم",
        "layout": "kick-game",
        "description": "Warm minimalist luxury streetwear — cream backgrounds, dense editorial grid, sneakers-as-art",
    },
    {
        "id": "street",
        "name": "Street Vibes",
        "nameAr": "ستريت",
        "layout": "street",
        "description": "Bold urban streetwear — vibrant yellow, topographic lines, chunky type",
    },
    {
        "id": "rabbitsocks",
        "name": "RabbitSocks",
        "nameAr": "رابيت سوكس",
        "layout": "rabbitsocks",
        "description": "Luxury minimalism — quiet luxury aesthetic with serif italic headlines, generous whitespace, and editorial photography",
    },
    {
        "id": "gilded-glamour-boutique",
        "name": "Gilded Glamour Boutique",
        "nameAr": "بوتيك الفخامة المُذهَّبة",
        "layout": "luxury-minimal",
        "description": "A bold, gold-accented luxury fashion theme with parallax hero, scroll-fill text animations, and curated vertical layouts.",
    },
    {
        "id": "bazar",
        "name": "Bazar",
        "nameAr": "بازار",
        "layout": "bazar",
        "description": "Bold streetwear aesthetic — vibrant amber/yellow palette, chunky uppercase typography, organic wavy shapes, and split product layouts",
    },
    {
        "id": "vionne",
        "name": "Vionne",
        "nameAr": "فيون",
        "layout": "vionne",
        "description": "Refined grayscale storefront for modest fashion. Crisp typography, slow fade slideshow, draggable before/after, and motion-led product cards.",
    },
    {
        "id": "saw-saw",
        "name": "Saw Saw — The Gilded Curator",
        "nameAr": "Saw Saw — The Gilded Curator",
        "layout": "default",
        "description": "High-end editorial luxury theme with intentional asymmetry, generous breathing room, and gold accents.",
    },
]


@lookup_router.get(
    "/themes",
    response_model=SuccessResponse,
    summary="List available storefront themes",
)
async def list_themes():
    """Return the list of available storefront themes for the dashboard.

    Pulls from the new public.themes table (theme engine v2) joined with the
    latest theme_version to get nameAr/layout out of the manifest. Falls back
    to the static AVAILABLE_THEMES list if the DB query fails or returns
    empty so the dashboard never sees an empty marketplace during a deploy.

    Each entry is then decorated with admin-config flags (required_plan,
    display_order, preview_image_url, demo_url) and themes flagged
    is_visible=false are filtered out before returning.
    """
    import logging

    from sqlalchemy import select

    from src.config.settings import get_settings
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.theme_admin_config import (
        ThemeAdminConfigModel,
    )
    from src.infrastructure.repositories.theme_repository import ThemeRepository
    from src.infrastructure.repositories.theme_version_repository import (
        ThemeVersionRepository,
    )

    settings = get_settings()
    assets_base = settings.storefront_assets_base_url.rstrip("/")

    # Load admin-config flags once. Missing rows → defaults (visible, free,
    # display_order=100). The admin GET endpoint upserts missing slugs, so
    # this map should be near-complete in steady state.
    admin_flags: dict[str, dict] = {}
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ThemeAdminConfigModel))
            for row in result.scalars().all():
                admin_flags[row.theme_slug] = {
                    "is_visible": row.is_visible,
                    "required_plan": row.required_plan,
                    "display_order": row.display_order,
                    "preview_image_url": row.preview_image_url,
                }
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "list_themes_admin_flags_unavailable",
            extra={"error": str(exc)},
        )

    def _decorate(entry: dict) -> dict | None:
        """Filter by visibility, then decorate with required_plan/display_order/asset URLs.

        Returns ``None`` for invisible themes — caller filters those out.
        """
        slug = entry.get("id", "")
        flags = admin_flags.get(
            slug,
            {
                "is_visible": True,
                "required_plan": "free",
                "display_order": 100,
                "preview_image_url": None,
            },
        )
        if not flags["is_visible"]:
            return None
        # Admin-uploaded preview overrides the convention URL when set.
        # Otherwise we fall back to {STOREFRONT_ASSETS_BASE_URL}/themes/{slug}/preview.png
        # so any screenshots checked into the storefront repo's public/ keep working.
        preview_url = (
            flags.get("preview_image_url") or f"{assets_base}/themes/{slug}/preview.png"
        )
        return {
            **entry,
            "required_plan": flags["required_plan"],
            "display_order": flags["display_order"],
            "preview_image_url": preview_url,
            # Single shared "demo" store rendered with the requested theme via
            # ?preview_theme=. The storefront reads that param in StoreContext
            # and overrides theme_settings.theme.base_theme so every theme can
            # be previewed against the same seeded sample products — no
            # per-theme demo subdomain to provision.
            "demo_url": (
                f"https://demo.{settings.storefront_base_domain}/?preview_theme={slug}"
            ),
        }

    def _build_payload(raw: list[dict]) -> list[dict]:
        decorated = [out for out in (_decorate(e) for e in raw) if out is not None]
        decorated.sort(key=lambda x: (x.get("display_order", 100), x.get("name", "")))
        return decorated

    try:
        async with AsyncSessionLocal() as session:
            theme_repo = ThemeRepository(session)
            version_repo = ThemeVersionRepository(session)
            db_themes = await theme_repo.list_published(
                type_filter=None, skip=0, limit=200
            )

            if not db_themes:
                return SuccessResponse(
                    data=_build_payload(AVAILABLE_THEMES),
                    message="Themes retrieved successfully",
                )

            # Batch-load latest version manifests to extract nameAr/layout
            latest_versions = await version_repo.get_latest_for_themes([
                t.id for t in db_themes
            ])

            payload = []
            for t in db_themes:
                manifest = (
                    latest_versions[t.id].manifest if t.id in latest_versions else {}
                )
                payload.append({
                    "id": t.slug,
                    "name": t.name,
                    "nameAr": manifest.get("nameAr")
                    or manifest.get("name_ar")
                    or t.name,
                    "layout": manifest.get("layout") or "default",
                    "description": t.description or "",
                })

            return SuccessResponse(
                data=_build_payload(payload),
                message="Themes retrieved successfully",
            )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "list_themes_db_fallback",
            extra={"error": str(exc)},
        )

    # Fallback: return the static list if the DB is empty/unreachable
    return SuccessResponse(
        data=_build_payload(AVAILABLE_THEMES),
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
# Store Lookup (subdomain + custom domain)
# ============================================================================


def _serialize_public_store(
    store,
    *,
    tenant_feature_flags: dict[str, bool] | None = None,
) -> dict:
    """Common payload returned by `/store-by-subdomain` and `/store-by-domain`.

    The Next.js storefront and the @numu/theme-sdk both consume this shape;
    keep new fields here additive so older client builds keep working.

    `tenant_feature_flags` is the per-tenant flag map loaded by the route
    (via `_read_feature_flags`). The storefront reads flags like
    `ff_storefront_promo_render` to skip rendering work for tenants that
    aren't in the offers-v2 phased rollout yet.
    """
    return {
        "id": str(store.id),
        "name": store.name,
        "slug": store.slug,
        "subdomain": store.subdomain,
        "custom_domain": store.custom_domain,
        "description": store.description,
        "logo_url": store.logo_url,
        "banner_url": store.banner_url,
        "status": store.status.value
        if hasattr(store.status, "value")
        else str(store.status),
        "settings": store.settings or {},
        "theme_settings": store.theme_settings,
        "business_hours": store.business_hours or {},
        "default_currency": store.default_currency.value
        if hasattr(store.default_currency, "value")
        else str(store.default_currency),
        "default_language": store.default_language,
        "social_links": store.social_links,
        "use_nextjs_storefront": getattr(store, "use_nextjs_storefront", False),
        "tenant_feature_flags": tenant_feature_flags or {},
    }


@lookup_router.get(
    "/store-by-subdomain/{subdomain}",
    response_model=SuccessResponse,
    summary="Get store info by subdomain",
    operation_id="get_store_by_subdomain",
)
async def get_store_by_subdomain(
    subdomain: Annotated[str, Path(description="Store subdomain")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Look up a store by its subdomain (public). Used by the Next.js
    storefront when the inbound hostname is `*.numueg.app`."""
    normalized = subdomain.lower()
    cached = await cache.get_store_by_subdomain(normalized)
    if cached == MISSING_SENTINEL:
        raise EntityNotFoundError("Store", subdomain, identifier_name="subdomain")
    if isinstance(cached, dict):
        return SuccessResponse(
            data=cached,
            message="Store retrieved successfully",
        )

    store = await store_repo.get_by_subdomain(normalized)
    if not store or store.status == StoreStatus.PENDING_APPROVAL:
        await cache.set_store_missing(subdomain=normalized)
        raise EntityNotFoundError("Store", subdomain, identifier_name="subdomain")

    flags = await _read_feature_flags(session, tenant_id=store.tenant_id)
    payload = _serialize_public_store(store, tenant_feature_flags=flags)
    await cache.set_store(payload)
    return SuccessResponse(
        data=payload,
        message="Store retrieved successfully",
    )


@lookup_router.get(
    "/store-by-domain/{domain}",
    response_model=SuccessResponse,
    summary="Get store info by custom domain",
    operation_id="get_store_by_domain",
)
async def get_store_by_domain(
    domain: Annotated[str, Path(description="Custom hostname, e.g. shop.brand.com")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Look up a store by its custom domain (public).

    The Next.js middleware passes the full inbound hostname when it does
    not end in the platform domain. Match is case-insensitive.
    """
    normalized = domain.lower().strip()
    if not normalized or "." not in normalized:
        raise EntityNotFoundError("Store", domain, identifier_name="domain")

    cached = await cache.get_store_by_domain(normalized)
    if cached == MISSING_SENTINEL:
        raise EntityNotFoundError("Store", domain, identifier_name="domain")
    if isinstance(cached, dict):
        return SuccessResponse(
            data=cached,
            message="Store retrieved successfully",
        )

    store = await store_repo.get_by_custom_domain(normalized)
    if not store or store.status == StoreStatus.PENDING_APPROVAL:
        await cache.set_store_missing(custom_domain=normalized)
        raise EntityNotFoundError("Store", domain, identifier_name="domain")

    flags = await _read_feature_flags(session, tenant_id=store.tenant_id)
    payload = _serialize_public_store(store, tenant_feature_flags=flags)
    await cache.set_store(payload)
    return SuccessResponse(
        data=payload,
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
            status=product.status.value
            if hasattr(product.status, "value")
            else product.status,
            price=str(product.price.amount),
            price_currency=product.price.currency.value,
            compare_at_price=str(product.compare_at_price.amount)
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
    summary="Get product by slug (or UUID)",
    operation_id="get_product_by_slug",
)
async def get_product_by_slug(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_slug: Annotated[str, Path(description="Product slug or UUID")],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Get a product by slug or UUID (public).

    We accept both so links minted by Next.js before the slug migration
    (bare UUIDs in `/product/<uuid>`) keep resolving, and the new
    `/product/<slug>` URLs work without a second round-trip.
    """
    # Verify store exists
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    product = None
    # Try UUID first — cheap string check before spending a parse exception.
    if len(product_slug) == 36 and product_slug.count("-") == 4:
        try:
            maybe_uuid = UUID(product_slug)
            product = await product_repo.get_by_id(maybe_uuid)
            # Scope the UUID result to THIS store so the route can't be
            # abused to probe products across tenants.
            if product and product.store_id != store_id:
                product = None
        except ValueError:
            product = None

    if product is None:
        product = await product_repo.get_by_slug(store_id, product_slug)

    if not product:
        raise EntityNotFoundError("Product", product_slug, identifier_name="slug")

    # Phase 8.1 — fetch variants for the PDP. Empty array possible
    # only on products that haven't been backfilled yet (shouldn't
    # happen post-migration); theme code branches on
    # `variants.length === 0` to fall back to product-level price.
    variant_summaries = await _resolve_variants_for_product(product.id)

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
            price=str(product.price.amount),
            price_currency=product.price.currency.value,
            compare_at_price=str(product.compare_at_price.amount)
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
            options=getattr(product, "options", None) or [],
            variants=variant_summaries,
            created_at=str(product.created_at),
            updated_at=str(product.updated_at),
        ),
        message="Product retrieved successfully",
    )


async def _resolve_variants_for_product(product_id: UUID) -> list[dict]:
    """Helper — load variants and shape into ProductVariantSummary dicts.

    Pydantic accepts dict-of-fields for nested model_validate; we
    return dicts rather than the schema class itself to keep the call
    site simple (no extra import).
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.variant_repository import VariantRepository

    async with AsyncSessionLocal() as session:
        repo = VariantRepository(session)
        variants = await repo.list_for_product(product_id)
    return [
        {
            "id": str(v.id),
            "position": v.position,
            "option_values": v.option_values or {},
            "price": str(v.price.amount),
            "price_currency": v.price.currency.value
            if hasattr(v.price.currency, "value")
            else str(v.price.currency),
            "compare_at_price": (
                str(v.compare_at_price.amount) if v.compare_at_price else None
            ),
            "sku": v.sku,
            "barcode": v.barcode,
            "inventory_quantity": v.inventory_quantity,
            "is_in_stock": v.is_in_stock,
            "image_url": v.image_url,
            "weight": v.weight,
        }
        for v in variants
    ]


class BackInStockSubscribeRequest(BaseModel):
    """Body for `POST /products/{id}/notify-back-in-stock` (Phase 3.5)."""

    email: EmailStr = Field(..., max_length=254, description="Recipient email")
    variant_id: UUID | None = Field(
        None,
        description=(
            "Specific variant to wait on (e.g. Large/Blue). When omitted, "
            "the subscription fires the moment the product-level stock "
            "flips back in."
        ),
    )


@router.post(
    "/products/{product_id}/notify-back-in-stock",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Subscribe an email to back-in-stock notifications",
    operation_id="notify_back_in_stock",
)
async def notify_back_in_stock(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    body_in: BackInStockSubscribeRequest,
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    sub_repo: Annotated[
        Any,
        Depends(get_product_subscription_repository),
    ],
):
    """Phase 3.5 — record a back-in-stock subscription.

    Idempotent: a second click for the same (product, variant, email)
    upserts to the existing row so the customer can't accidentally
    flood the queue. Returns 202 (request accepted) without confirming
    whether the email is new or existing — that distinction is internal.

    The Celery sweep task (`product_subscription_sweep`) handles
    delivery on its hourly schedule. Out of scope for this endpoint:
    we never email synchronously here, since a single product flipping
    in-stock could fan out to thousands of subscribers.

    Returns 200 with `{ status: "subscribed" }` on success even when
    the product is currently in-stock — the customer's intent ("notify
    me next time it goes back in stock") is preserved for the next
    stockout cycle. The Celery sweep skips currently-in-stock products
    that haven't seen a stockout-then-restock cycle.
    """
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    product = await product_repo.get_by_id(product_id)
    if not product or product.store_id != store_id:
        raise EntityNotFoundError("Product", str(product_id))

    await sub_repo.upsert_subscription(
        tenant_id=store.tenant_id or store_id,
        store_id=store_id,
        product_id=product_id,
        variant_id=body_in.variant_id,
        email=body_in.email,
    )

    return SuccessResponse(
        data={"status": "subscribed"},
        message="You'll be notified when this product is back in stock.",
    )


@router.get(
    "/products/{product_id}/related",
    summary="Related products (same category, excluding self)",
    operation_id="get_related_products",
)
async def get_related_products(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    limit: int = Query(4, ge=1, le=24),
):
    """Phase 3.3 — same-category-minus-self recommendations.

    The SDK's `useRelatedProducts(productId)` consumes this. The
    recommendation is intentionally simple in v1 (same category, drop
    the source); collaborative filtering / "frequently bought together"
    lands in Phase 4 once the funnel-events table has enough volume to
    train against.

    Returns an empty `items` array (HTTP 200) when:
      - the product has no category, OR
      - the category has no other products yet, OR
      - the category exists but every sibling is inactive.

    Themes branch on `items.length` and either render the section or
    skip it — they should NOT show an error UI for an empty result.
    """
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    source = await product_repo.get_by_id(product_id)
    if not source or source.store_id != store_id:
        raise EntityNotFoundError("Product", str(product_id))

    # No category → no recommendation surface. Empty list rather than
    # 400 so the SDK hook can render unconditionally and skip.
    if not source.category_id:
        return SuccessResponse(
            data={"items": [], "total": 0},
            message="No related products",
        )

    # Pull a slightly larger page than `limit` so we have headroom to
    # drop the source product without making a second call. by_category
    # already filters to active products via the storefront use case.
    use_case = ListProductsUseCase(product_repository=product_repo)
    fetched = await use_case.by_category(
        category_id=source.category_id,
        page=1,
        page_size=limit + 1,
    )
    siblings = [p for p in fetched.items if p.id != product_id][:limit]

    items: list[dict] = []
    for product in siblings:
        items.append({
            "id": str(product.id),
            "store_id": str(product.store_id),
            "name": product.name,
            "slug": product.slug,
            "description": product.description,
            "short_description": product.short_description,
            "price": str(product.price),
            "price_currency": product.price_currency,
            "compare_at_price": str(product.compare_at_price)
            if product.compare_at_price
            else None,
            "sku": product.sku,
            "quantity": product.quantity,
            "is_in_stock": product.is_in_stock,
            "is_on_sale": product.is_on_sale,
            "category_id": str(product.category_id) if product.category_id else None,
            "images": product.images,
            "tags": product.tags,
        })

    return SuccessResponse(
        data={"items": items, "total": len(items)},
        message="Related products retrieved",
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
# Customer Password Reset
# ============================================================================


class CustomerForgotPasswordRequest(BaseModel):
    email: str = Field(..., max_length=254)


class CustomerResetPasswordRequest(BaseModel):
    email: str = Field(..., max_length=254)
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=12, max_length=128)


@router.post(
    "/auth/forgot-password",
    response_model=SuccessResponse,
    summary="Request customer password reset",
    operation_id="customer_forgot_password",
)
async def customer_forgot_password(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CustomerForgotPasswordRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Send a 6-digit password reset code to the customer's email."""
    import random

    from src.core.value_objects.email import Email as EmailVO
    from src.infrastructure.cache.redis_cache import RedisCacheService
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    # Always return success to prevent email enumeration
    try:
        email_vo = EmailVO(value=request.email)
    except Exception:
        return SuccessResponse(
            data=None, message="If the email exists, a reset code has been sent"
        )

    customer = await customer_repo.get_by_email(store_id, email_vo)
    if customer and customer.has_account:
        code = f"{random.randint(0, 999999):06d}"
        cache = RedisCacheService()
        await cache.set(
            f"customer_password_reset:{store_id}:{request.email}",
            code,
            expire=900,  # 15 minutes
        )
        try:
            from src.core.interfaces.services.email_service import EmailMessage
            from src.infrastructure.external_services.resend.email_templates.transactional import (
                otp_code_email,
            )

            email_service = ResendEmailService()
            tpl = otp_code_email(
                code=code, purpose="password_reset", expires_minutes=15
            )
            await email_service.send_email(
                EmailMessage(
                    to=request.email,
                    subject=tpl["subject"],
                    html_content=tpl["html"],
                )
            )
        except Exception:
            pass  # Silently fail to prevent info leak

    return SuccessResponse(
        data=None, message="If the email exists, a reset code has been sent"
    )


@router.post(
    "/auth/reset-password",
    response_model=SuccessResponse,
    summary="Reset customer password with code",
    operation_id="customer_reset_password",
)
async def customer_reset_password(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CustomerResetPasswordRequest,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
):
    """Verify reset code and update customer password."""
    from src.core.value_objects.email import Email as EmailVO
    from src.infrastructure.cache.redis_cache import RedisCacheService

    cache = RedisCacheService()
    cache_key = f"customer_password_reset:{store_id}:{request.email}"
    stored_code = await cache.get(cache_key)

    if not stored_code or stored_code != request.code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="كود التحقق غير صحيح أو منتهي الصلاحية",
        )

    try:
        email_vo = EmailVO(value=request.email)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address",
        )

    customer = await customer_repo.get_by_email(store_id, email_vo)
    if not customer or not customer.has_account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="كود التحقق غير صحيح أو منتهي الصلاحية",
        )

    hashed = password_service.hash_password(request.new_password)
    customer.update_password(hashed)
    await customer_repo.update(customer)

    # Clean up the reset code
    await cache.delete(cache_key)

    return SuccessResponse(data=None, message="Password has been reset successfully")


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

    # Phase 5.1 — rotation reuse detection. The refresh token carries
    # a jti; once consumed it's blacklisted in Redis with a TTL
    # matching the token's remaining lifetime. A second use of the
    # same jti = signal of theft. We reject + clear cookies + log so
    # the legitimate user re-logs in (their next refresh fails the
    # same check, which is the intended behavior — both stolen and
    # legitimate sessions die at the suspicious event).
    from src.application.services.refresh_token_blacklist_service import (
        RefreshTokenBlacklistService,
    )
    from src.config.logging_config import get_logger as _get_logger
    from src.infrastructure.cache.redis_cache import RedisCacheService

    blacklist = RefreshTokenBlacklistService(RedisCacheService())
    if payload.jti and await blacklist.is_used(payload.jti):
        _get_logger(__name__).warning(
            "customer_refresh_token_reuse_detected",
            customer_id=str(payload.customer_id),
            store_id=str(payload.store_id),
            jti=payload.jti,
        )
        clear_customer_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected — please sign in again.",
        )

    customer = await customer_repo.get_by_id(payload.customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Customer not found",
        )

    # Mark the consumed jti as used BEFORE issuing fresh tokens so a
    # narrow race (two near-simultaneous refreshes from the same
    # cookie) doesn't double-issue. The losing call sees the jti
    # already blacklisted and 401s — the client retries with the
    # fresh refresh that the winning call set.
    if payload.jti:
        await blacklist.mark_used(payload.jti, payload.exp)

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

    # In non-production environments, surface methods that are merely `enabled`
    # (without `is_configured`) so merchants see what they selected during onboarding
    # before they've finished credential setup.
    from src.config import settings as app_settings

    def _show(provider: str) -> bool:
        cfg = payment_settings.get(provider, {})
        if not cfg.get("enabled"):
            return False
        if cfg.get("is_configured"):
            return True
        return app_settings.environment != "production"

    methods = []
    if _show("cod"):
        methods.append({
            "id": "cod",
            "label": "الدفع عند الاستلام",
            "label_en": "Cash on Delivery",
            "type": "cod",
        })
    if _show("paymob"):
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
    if _show("fawry"):
        methods.append({
            "id": "fawry",
            "label": "فوري",
            "label_en": "Fawry",
            "type": "fawry",
        })

    # Kashier uses tenant credential system — check if configured
    if _show("kashier"):
        methods.append({
            "id": "kashier",
            "label": "بطاقة بنكية",
            "label_en": "Credit/Debit Card",
            "type": "kashier",
        })

    if _show("fawaterak"):
        methods.append({
            "id": "fawaterak",
            "label": "فواتيرك",
            "label_en": "Fawaterak",
            "type": "fawaterak",
        })

    # InstaPay — customers transfer to the merchant's IPA from their
    # bank app and upload a proof screenshot. No external redirect.
    if _show("instapay"):
        methods.append({
            "id": "instapay",
            "label": "انستاباي",
            "label_en": "InstaPay",
            "type": "instapay",
        })

    # COD deposit-to-confirm policy. The storefront renders a deposit
    # section below the COD radio when this is non-null, asking the
    # customer to pick one of the allowed gateways. Intersect with
    # currently-shown methods so we don't advertise a deposit gateway
    # the merchant has since disabled.
    deposit_payload = None
    cod_block = payment_settings.get("cod") or {}
    deposit_raw = cod_block.get("deposit_policy") or {}
    if (
        cod_block.get("enabled")
        and deposit_raw.get("enabled")
        and int(deposit_raw.get("amount_cents", 0) or 0) > 0
    ):
        policy_gateways = list(deposit_raw.get("allowed_gateways") or [])
        # Determine which of the merchant's allowed gateways are
        # actually available to the customer right now. `_show` is
        # the same gate as the methods list above.
        live_gateways = [g for g in policy_gateways if _show(g)]
        if live_gateways:
            deposit_payload = {
                "amount_cents": int(deposit_raw["amount_cents"]),
                "ttl_minutes": int(deposit_raw.get("ttl_minutes", 30) or 30),
                "allowed_gateways": live_gateways,
            }

    return SuccessResponse(
        data={
            "methods": methods,
            # Null when no deposit policy is active; populated when
            # the merchant requires a deposit to confirm COD orders.
            "cod_deposit_policy": deposit_payload,
        },
        message="Payment methods retrieved",
    )

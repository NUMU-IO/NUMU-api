"""Product routes nested under stores.

URL: /stores/{store_id}/products
"""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from src.api.dependencies import (
    get_image_pipeline,
    get_onboarding_repository,
    get_product_cache_service,
    get_product_repository,
    get_storage_service,
    get_store_repository,
    verify_store_ownership,
)
from src.api.dependencies.plan import require_product_limit
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_csv_upload, validate_image_upload
from src.api.v1.schemas import (
    CreateProductRequest,
    DeleteImageRequest,
    ImportResultResponse,
    ImportRowErrorResponse,
    PaginatedListResponse,
    ProductResponse,
    UpdateProductRequest,
    UploadedImageResponse,
)
from src.application.dto.product import CreateProductDTO, UpdateProductDTO
from src.application.use_cases.products import (
    CreateProductUseCase,
    DeleteProductImageUseCase,
    DeleteProductUseCase,
    ExportProductsUseCase,
    GetProductUseCase,
    ImportProductsUseCase,
    UpdateProductUseCase,
    UploadProductImageUseCase,
)
from src.application.use_cases.products.upload_image import UploadProductImageDTO
from src.core.entities.product import ProductStatus
from src.core.entities.store import Store
from src.infrastructure.cache import ProductCacheService
from src.infrastructure.events.setup import get_event_bus
from src.infrastructure.external_services.cloudflare_r2 import (
    CloudflareR2StorageService,
)
from src.infrastructure.external_services.image import ImagePipeline
from src.infrastructure.repositories import (
    OnboardingRepository,
    ProductRepository,
    StoreRepository,
)

router = APIRouter(prefix="/{store_id}/products")

PRODUCT_SORT_FIELDS = {"name", "price", "created_at", "updated_at", "quantity"}


@router.post(
    "/",
    response_model=SuccessResponse[ProductResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new product",
    operation_id="create_product",
    dependencies=[Depends(require_product_limit())],
)
async def create_product(
    request: CreateProductRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Create a new product for the store."""
    # SKU policy: blank → the platform generates a stable store-unique code
    # (never regenerated later); provided → duplicate rejected with a
    # bilingual 409 instead of surfacing as a silent shadow or a 500.
    from src.application.services.sku_service import (
        duplicate_sku_error_detail,
        generate_unique_sku,
        sku_in_use,
    )

    sku_in = (request.sku or "").strip() or None
    if sku_in is not None:
        if await sku_in_use(product_repo.session, store.id, sku_in):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=duplicate_sku_error_detail(sku_in),
            )
    else:
        sku_in = await generate_unique_sku(product_repo.session, store.id)

    use_case = CreateProductUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
        onboarding_repository=onboarding_repo,
        event_bus=get_event_bus(),
    )

    dto = CreateProductDTO(
        name=request.name,
        slug=request.slug,
        sku=sku_in,
        description=request.description,
        short_description=request.short_description,
        product_type=request.product_type,
        status=request.status,
        price=request.price,
        price_currency=request.price_currency,
        compare_at_price=request.compare_at_price,
        cost_price=request.cost_price,
        quantity=request.quantity,
        low_stock_threshold=request.low_stock_threshold,
        images=request.images,
        category_id=request.category_id,
        tags=request.tags,
        attributes=request.attributes,
        seo_title=request.seo_title,
        seo_description=request.seo_description,
        template_suffix=request.template_suffix,
    )

    result = await use_case.execute(
        dto=dto,
        store_id=store.id,
        user_id=store.owner_id,
    )

    # Step 12 — flush the storefront's ISR cache for this product so
    # the new row shows up on the PDP / PLP without waiting out the
    # 60s revalidate window. Best-effort: failure is logged inside
    # the helper, never raised.
    if store.subdomain:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_product_change,
        )

        await revalidate_on_product_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
            product_slug=result.slug,
            product_id=str(result.id),
        )

    # Phase 8.1 — materialize options + variants. UI #2 sends top-level
    # options/variants; the main "Product Options" + "Variant Combinations"
    # editor sends them inside `attributes` — bridge those into the canonical
    # model so a combination becomes a real variant. When nothing is sent the
    # migration's default-variant pattern creates one row carrying the product's
    # price + quantity, so cart variant-id resolution always finds a row.
    options_in = request.options
    variants_in = request.variants
    if not options_in and not variants_in:
        b_opts, b_vars = _options_variants_from_legacy_attributes(result.attributes)
        if b_opts is not None or b_vars is not None:
            options_in = b_opts or []
            variants_in = b_vars or []
    variant_summaries = await _materialize_product_variants(
        session=product_repo.session,
        product_id=result.id,
        store_id=store.id,
        tenant_id=store.tenant_id,
        options=options_in,
        variants=variants_in,
        default_price=str(result.price),
        default_currency=result.price_currency,
        default_quantity=result.quantity,
        default_sku=result.sku,
    )
    # Headline count := SUM of the materialized variants (no-op for the
    # default-variant case, where the sum IS the submitted quantity).
    from src.application.services.variant_sync_service import (
        recompute_product_quantity,
    )

    await recompute_product_quantity(product_repo.session, product_id=result.id)

    # Serve the post-rollup quantity — the use-case result predates the
    # materialization above (same freshness rule as the update route).
    from src.infrastructure.database.models.tenant.product import ProductModel

    _fresh = await product_repo.session.get(ProductModel, result.id)
    fresh_quantity = _fresh.quantity if _fresh is not None else result.quantity

    return SuccessResponse(
        data=ProductResponse(
            id=str(result.id),
            store_id=str(result.store_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            short_description=result.short_description,
            product_type=result.product_type,
            status=result.status,
            price=str(result.price),
            price_currency=result.price_currency,
            compare_at_price=str(result.compare_at_price)
            if result.compare_at_price
            else None,
            cost_price=str(result.cost_price) if result.cost_price else None,
            sku=result.sku,
            quantity=fresh_quantity,
            is_in_stock=fresh_quantity > 0,
            is_low_stock=result.is_low_stock,
            is_on_sale=result.is_on_sale,
            category_id=str(result.category_id) if result.category_id else None,
            images=result.images,
            tags=result.tags,
            attributes=result.attributes,
            seo_title=result.seo_title,
            seo_description=result.seo_description,
            template_suffix=result.template_suffix,
            options=[o.model_dump() for o in (options_in or [])],
            variants=variant_summaries,
            created_at=str(result.created_at),
            updated_at=str(result.updated_at),
        ),
        message="Product created successfully",
    )


def _legacy_variant_snapshot(attributes) -> tuple:
    """The comparable identity of the legacy variant data inside an
    ``attributes`` dict: (variants, variant_combinations) as canonical JSON
    strings, or (None, None) when neither key holds a list. Used to detect
    whether an update actually CHANGED the legacy card's data versus the
    hub's routine resend of what it loaded."""
    import json

    if not isinstance(attributes, dict):
        return (None, None)
    axes = attributes.get("variants")
    combos = attributes.get("variant_combinations")
    if not isinstance(axes, list) and not isinstance(combos, list):
        return (None, None)
    # Empty lists carry no bridgeable data — treat them as absent so a
    # simple product's `variants: []` never registers as a legacy edit.
    axes_key = (
        json.dumps(axes, sort_keys=True) if isinstance(axes, list) and axes else None
    )
    combos_key = (
        json.dumps(combos, sort_keys=True)
        if isinstance(combos, list) and combos
        else None
    )
    return (axes_key, combos_key)


def _options_variants_from_legacy_attributes(attributes):
    """Bridge the main product editor's legacy variant shape into canonical
    Phase-8.1 options + variants, so a "Variant Combination" becomes a real,
    purchasable product_variant (Shopify parity).

    The main editor ("Product Options" + "Variant Combinations") persists
    variants inside `attributes`, never the top-level options/variants:
        attributes.variants             = axes   [{name, options:[values], ...}]
        attributes.variant_combinations = combos
            [{options:{axis:value}, price, stock, sku, enabled}]
    so its combinations never became real product_variants and the storefront
    / cart / checkout (which read product.options + product_variants) couldn't
    resolve a buyable variant. Derive options + variants here so the existing
    materializer creates them.

    Returns (None, None) when there is nothing to bridge, so non-variant
    updates leave the variant matrix untouched.
    """
    from decimal import Decimal, InvalidOperation

    from src.api.v1.schemas.tenant.product import (
        ProductOptionInput,
        VariantInput,
    )

    if not isinstance(attributes, dict):
        return None, None
    legacy_axes = attributes.get("variants")
    combos = attributes.get("variant_combinations")
    if not isinstance(legacy_axes, list) and not isinstance(combos, list):
        return None, None

    options: list = []
    for i, axis in enumerate(legacy_axes or []):
        if not isinstance(axis, dict):
            continue
        name = str(axis.get("name") or "").strip()
        values = [str(v).strip() for v in (axis.get("options") or []) if str(v).strip()]
        if name and values:
            options.append(ProductOptionInput(name=name, position=i, values=values))

    variants: list = []
    for j, combo in enumerate(combos or []):
        if not isinstance(combo, dict):
            continue
        if combo.get("enabled") is False:
            continue
        raw_ov = combo.get("options")
        if not isinstance(raw_ov, dict) or not raw_ov:
            continue
        # Match the axis name/value casing exactly (the SDK matches with a
        # strict `===` + key-count check), trimmed of stray whitespace.
        option_values = {str(k).strip(): str(val).strip() for k, val in raw_ov.items()}
        # Combo prices are MAJOR units (10 = 10 EGP) — the same convention
        # as every other variant write path: Money(amount=majors), the repo
        # persists .cents. A historical ×100 here double-converted (majors
        # were treated as cents and multiplied again), storing bridge-created
        # variant prices 100× too high; migration 20260718 normalizes those.
        try:
            price = Decimal(str(combo.get("price") or "0"))
        except (InvalidOperation, ValueError, TypeError):
            price = Decimal("0")
        try:
            stock = int(float(combo.get("stock") or 0))
        except (TypeError, ValueError):
            stock = 0
        sku_raw = combo.get("sku")
        sku = str(sku_raw).strip() if sku_raw else None
        variants.append(
            VariantInput(
                position=j,
                option_values=option_values,
                price=price,
                inventory_quantity=max(0, stock),
                sku=sku or None,
            )
        )

    if not options and not variants:
        return None, None
    return options, variants


async def _materialize_product_variants(
    *,
    session,
    product_id: UUID,
    store_id: UUID,
    tenant_id: UUID,
    options,
    variants,
    default_price: str,
    default_currency: str,
    default_quantity: int,
    default_sku: str | None,
) -> list[dict]:
    """Persist options + variants for a product (create + update).

    Runs on the caller's request-scoped session so the variant inserts
    see the product row inserted earlier in the same transaction —
    opening a separate session here causes a FK violation because the
    product INSERT hasn't committed yet.

    Logic:
    1. Write `options` JSONB onto the product row.
    2. If `variants` is empty → ensure a single default variant exists
       carrying the product's headline price + quantity + SKU.
    3. Otherwise, upsert each variant in the list: rows whose `id` is
       present get updated in-place; rows without an `id` are created.
    4. Variants present in the DB but absent from the request are
       hard-deleted (the merchant intends them gone).
    """
    from src.core.value_objects.money import Money
    from src.infrastructure.database.models.tenant.product import ProductModel
    from src.infrastructure.repositories.variant_repository import VariantRepository

    # 1. Patch options onto the product row.
    prod_row = await session.get(ProductModel, product_id)
    if prod_row is not None:
        prod_row.options = [o.model_dump() for o in (options or [])]
        await session.flush()

    repo = VariantRepository(session)
    existing = await repo.list_for_product(product_id)
    existing_by_id = {v.id: v for v in existing}

    # SKU policy for variant rows: a NEW row with no SKU gets a generated
    # one (same stable format as products); existing rows are never
    # regenerated. One policy across product create, import, and matrix.
    from src.application.services.sku_service import generate_unique_sku

    if not variants:
        # No variants in the request: keep the existing default
        # variant if there's exactly one and it has no option_values.
        if any(v.option_values for v in existing):
            # Multi-axis product but no variants submitted — drop
            # them all. The caller will have to submit explicit
            # variants on the next update to recreate them.
            for v in existing:
                await repo.delete_by_id(v.id)
        if not existing or any(v.option_values for v in existing):
            # Create a single default variant if none remains.
            v = await repo.create(
                tenant_id=tenant_id,
                store_id=store_id,
                product_id=product_id,
                position=0,
                option_values={},
                price=Money(
                    amount=int(float(default_price)), currency=default_currency
                ),
                sku=default_sku or await generate_unique_sku(session, store_id),
                inventory_quantity=default_quantity,
            )
        else:
            v = existing[0]
        return [_variant_to_summary_dict(v)]

    # Reuse existing rows for id-less variants by their option_values, so the
    # main-editor bridge (which can't carry variant ids) updates the same
    # product_variant rows each save instead of delete+recreating them — which
    # would churn ids and orphan order/line references.
    if any(vin.id is None for vin in variants):

        def _ov_sig(ov: dict | None) -> tuple:
            return tuple(sorted((ov or {}).items()))

        taken: set = set()
        existing_by_sig: dict = {}
        for ev in existing:
            existing_by_sig.setdefault(_ov_sig(ev.option_values), ev)
        for vin in variants:
            if vin.id is None:
                m = existing_by_sig.get(_ov_sig(vin.option_values))
                if m is not None and m.id not in taken:
                    vin.id = m.id
                    taken.add(m.id)

    # 4. Delete variants the request omitted.
    keep_ids = {v.id for v in variants if v.id is not None}
    for existing_v in existing:
        if existing_v.id not in keep_ids:
            await repo.delete_by_id(existing_v.id)

    # 3. Upsert each variant in the request.
    result_variants = []
    for idx, vin in enumerate(variants):
        price = Money(
            amount=int(float(vin.price)),
            currency=vin.price_currency or default_currency,
        )
        compare_at = (
            Money(amount=int(float(vin.compare_at_price)), currency=price.currency)
            if vin.compare_at_price is not None
            else None
        )
        cost = (
            Money(amount=int(float(vin.cost_price)), currency=price.currency)
            if vin.cost_price is not None
            else None
        )
        if vin.id and vin.id in existing_by_id:
            v = existing_by_id[vin.id]
            v.position = vin.position if vin.position is not None else idx
            v.option_values = vin.option_values or {}
            v.price = price
            v.compare_at_price = compare_at
            v.cost_price = cost
            # Manual-or-auto SKU rule: a submitted SKU wins; a blank one
            # keeps the row's existing SKU (never blanked, never
            # regenerated); a row that never had one gets generated.
            v.sku = vin.sku or v.sku or await generate_unique_sku(session, store_id)
            v.barcode = vin.barcode
            v.inventory_quantity = vin.inventory_quantity
            v.image_url = vin.image_url
            v.weight = vin.weight
            v = await repo.update(v)
        else:
            v = await repo.create(
                tenant_id=tenant_id,
                store_id=store_id,
                product_id=product_id,
                position=vin.position if vin.position is not None else idx,
                option_values=vin.option_values or {},
                price=price,
                compare_at_price=compare_at,
                cost_price=cost,
                sku=vin.sku or await generate_unique_sku(session, store_id),
                barcode=vin.barcode,
                inventory_quantity=vin.inventory_quantity,
                image_url=vin.image_url,
                weight=vin.weight,
            )
        result_variants.append(v)
    return [_variant_to_summary_dict(v) for v in result_variants]


def _variant_to_summary_dict(v) -> dict:
    return {
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


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[ProductResponse]],
    summary="List products",
    operation_id="list_products",
)
async def list_products(
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    category_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    product_status: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    sku: str | None = Query(None, description="Filter by SKU (partial match)"),
    price_min: int | None = Query(None, ge=0, description="Minimum price in cents"),
    price_max: int | None = Query(None, ge=0, description="Maximum price in cents"),
    sort_by: str | None = Query(
        None, description="Sort field: name, price, created_at, updated_at, quantity"
    ),
    sort_order: str = Query("asc", description="Sort direction: asc or desc"),
):
    """List products for a store with optional filtering, search, and sorting."""
    # Validate sort parameters against whitelist
    if sort_by is not None and sort_by not in PRODUCT_SORT_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid sort field '{sort_by}'. "
                f"Allowed: {', '.join(sorted(PRODUCT_SORT_FIELDS))}"
            ),
        )
    if sort_order not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sort_order must be 'asc' or 'desc'.",
        )
    # Resolve the 3-state status filter once. Anything outside the enum is
    # silently ignored — clients sometimes pass the legacy "active" string
    # which is also the canonical enum value, so that path just works.
    status_filter: ProductStatus | None = None
    if product_status:
        try:
            status_filter = ProductStatus(product_status)
        except ValueError:
            # Unknown status value → treat as "no filter" rather than 400
            # (the merchant hub's "All" tab sends no status, so a typo on a
            # bookmarked URL shouldn't blow up the page).
            status_filter = None

    # Single filter path — previously the endpoint branched between an
    # advanced path, a category path, and a default path. Only the advanced
    # path consulted `product_status`, so clicking Draft/Archived/Individual
    # from the merchant hub silently returned every product when no search
    # or sku/price filter was active. Now every list call goes through
    # list_with_filters so status + category + sort are always honoured.
    skip = (page - 1) * limit
    items = await product_repo.list_with_filters(
        store_id=store.id,
        category_id=category_id,
        skip=skip,
        limit=limit,
        status_filter=status_filter,
        search=search,
        sku=sku,
        price_min=price_min,
        price_max=price_max,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    total = await product_repo.count_with_filters(
        store_id=store.id,
        category_id=category_id,
        status_filter=status_filter,
        search=search,
        sku=sku,
        price_min=price_min,
        price_max=price_max,
    )

    from dataclasses import dataclass

    from src.application.dto.product import ProductDTO

    @dataclass
    class _Result:
        items: list
        total: int

    result = _Result(items=[ProductDTO.from_entity(p) for p in items], total=total)

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
            price_currency=getattr(
                product, "price_currency", store.default_currency or "EGP"
            ),
            compare_at_price=str(product.compare_at_price)
            if product.compare_at_price
            else None,
            cost_price=str(product.cost_price) if product.cost_price else None,
            sku=product.sku,
            quantity=product.quantity,
            is_in_stock=product.is_in_stock,
            is_low_stock=product.is_low_stock,
            is_on_sale=product.is_on_sale,
            category_id=str(product.category_id) if product.category_id else None,
            images=product.images,
            tags=product.tags,
            attributes=product.attributes,
            seo_title=product.seo_title,
            seo_description=product.seo_description,
            template_suffix=product.template_suffix,
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
    "/{product_id}",
    response_model=SuccessResponse[ProductResponse],
    summary="Get product by ID",
    operation_id="get_product",
)
async def get_product(
    product_id: Annotated[UUID, Path(description="Product ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Get product details by ID."""
    use_case = GetProductUseCase(product_repository=product_repo)

    result = await use_case.execute(product_id=product_id, store_id=store.id)

    # Hydrate the canonical variant model so the hub's merged editor can
    # load axes + matrix in one fetch (the detail response used to omit
    # both, forcing a second request and stale-looking empty lists).
    from src.infrastructure.database.models.tenant.product import ProductModel
    from src.infrastructure.repositories.variant_repository import VariantRepository

    prod_row = await product_repo.session.get(ProductModel, result.id)
    product_options = (prod_row.options if prod_row is not None else None) or []
    variant_summaries = [
        _variant_to_summary_dict(v)
        for v in await VariantRepository(product_repo.session).list_for_product(
            result.id
        )
    ]

    return SuccessResponse(
        data=ProductResponse(
            id=str(result.id),
            store_id=str(result.store_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            short_description=result.short_description,
            product_type=result.product_type,
            status=result.status,
            price=str(result.price),
            price_currency=result.price_currency,
            compare_at_price=str(result.compare_at_price)
            if result.compare_at_price
            else None,
            cost_price=str(result.cost_price) if result.cost_price else None,
            sku=result.sku,
            quantity=result.quantity,
            is_in_stock=result.is_in_stock,
            is_low_stock=result.is_low_stock,
            is_on_sale=result.is_on_sale,
            category_id=str(result.category_id) if result.category_id else None,
            images=result.images,
            tags=result.tags,
            attributes=result.attributes,
            seo_title=result.seo_title,
            seo_description=result.seo_description,
            template_suffix=result.template_suffix,
            options=product_options,
            variants=variant_summaries,
            created_at=str(result.created_at),
            updated_at=str(result.updated_at),
        ),
        message="Product retrieved successfully",
    )


@router.patch(
    "/{product_id}",
    response_model=SuccessResponse[ProductResponse],
    summary="Update product",
    operation_id="update_product",
)
async def update_product(
    product_id: Annotated[UUID, Path(description="Product ID")],
    request: UpdateProductRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update product details."""
    # Snapshot the legacy variant keys BEFORE the update overwrites
    # `attributes`, so we can tell a REAL legacy-card edit apart from the
    # hub's routine resend of what it loaded (the main form always sends
    # the full attributes back). Bridging on every save used to clobber
    # SKU-card / variants-API edits with the stale JSONB copy.
    prior = await product_repo.get_by_id(product_id)
    prior_legacy = (
        _legacy_variant_snapshot(prior.attributes)
        if prior is not None
        else (None, None)
    )

    # SKU policy on edit: duplicates 409 (bilingual); an existing SKU is
    # never auto-regenerated — SKUs are external identifiers.
    if request.sku is not None and (request.sku or "").strip():
        from src.application.services.sku_service import (
            duplicate_sku_error_detail,
            sku_in_use,
        )

        _sku = request.sku.strip()
        if await sku_in_use(
            product_repo.session, store.id, _sku, exclude_product_id=product_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=duplicate_sku_error_detail(_sku),
            )

    use_case = UpdateProductUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
        event_bus=get_event_bus(),
    )

    dto = UpdateProductDTO(
        name=request.name,
        slug=request.slug,
        sku=request.sku,
        description=request.description,
        short_description=request.short_description,
        price=request.price,
        compare_at_price=request.compare_at_price,
        cost_price=request.cost_price,
        quantity=request.quantity,
        low_stock_threshold=request.low_stock_threshold,
        images=request.images,
        category_id=request.category_id,
        tags=request.tags,
        attributes=request.attributes,
        status=request.status,
        seo_title=request.seo_title,
        seo_description=request.seo_description,
        template_suffix=request.template_suffix,
        # Distinguish an explicit ``template_suffix: null`` (clear the override)
        # from an omitted field (leave it alone) so a partial PATCH never wipes
        # the merchant's template variant.
        template_suffix_provided="template_suffix" in request.model_fields_set,
    )

    result = await use_case.execute(
        product_id=product_id,
        dto=dto,
        user_id=store.owner_id,
        store_id=store.id,
    )

    # Step 12 — flush ISR cache for the updated product. If the slug
    # was changed in this PATCH, we'd want to also flush the OLD slug
    # so a customer with the old URL cached doesn't see stale data —
    # the use case doesn't surface the prior slug today, so we accept
    # 60s lag on the old URL (TTL fallback). Best-effort.
    if store.subdomain:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_product_change,
        )

        await revalidate_on_product_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
            product_slug=result.slug,
            product_id=str(result.id),
        )

    # Phase 8.1 — re-materialize options/variants when the merchant sent them.
    # UI #2 ("SKU-tracked variants") sends the top-level options/variants; the
    # main "Product Options" + "Variant Combinations" editor sends them only
    # inside `attributes`. The legacy bridge now runs ONLY when the request's
    # own attributes carry variant data that DIFFERS from what was stored —
    # i.e. the merchant actually edited the legacy card. The hub's routine
    # resend of unchanged attributes (or a PATCH with no attributes at all)
    # no longer re-materializes from the stale JSONB, which used to clobber
    # every edit made through the variants API/card.
    options_in = request.options
    variants_in = request.variants
    if options_in is None and variants_in is None and request.attributes is not None:
        request_legacy = _legacy_variant_snapshot(request.attributes)
        if request_legacy != prior_legacy and request_legacy != (None, None):
            b_opts, b_vars = _options_variants_from_legacy_attributes(
                request.attributes
            )
            if b_opts is not None or b_vars is not None:
                options_in = b_opts or []
                variants_in = b_vars or []

    variant_summaries: list[dict] | None = None
    if options_in is not None or variants_in is not None:
        variant_summaries = await _materialize_product_variants(
            session=product_repo.session,
            product_id=result.id,
            store_id=store.id,
            tenant_id=store.tenant_id,
            options=options_in or [],
            variants=variants_in or [],
            default_price=str(result.price),
            default_currency=result.price_currency,
            default_quantity=result.quantity,
            default_sku=result.sku,
        )
        # Variant rows are now the stock authority — roll their total back
        # up into the headline count so list pages agree with the matrix.
        from src.application.services.variant_sync_service import (
            recompute_product_quantity,
        )

        await recompute_product_quantity(product_repo.session, product_id=result.id)
    else:
        # No variant data in the request: write the simple product's
        # headline fields (sku / quantity / price) through to its default
        # variant — the row cart availability and checkout debits read.
        # Multi-variant products are left alone (safe no-op inside).
        from src.application.services.variant_sync_service import (
            sync_simple_product_to_variant,
        )
        from src.infrastructure.repositories.variant_repository import (
            VariantRepository,
        )

        await sync_simple_product_to_variant(product_repo.session, product_id=result.id)
        variant_summaries = [
            _variant_to_summary_dict(v)
            for v in await VariantRepository(product_repo.session).list_for_product(
                result.id
            )
        ]

    # The use-case result predates the variant materialization/sync above,
    # so its headline fields can lag by one rollup. Serve the row's current
    # quantity and sku so the response the hub renders is never stale.
    from src.infrastructure.database.models.tenant.product import ProductModel

    _fresh = await product_repo.session.get(ProductModel, result.id)
    fresh_quantity = _fresh.quantity if _fresh is not None else result.quantity
    fresh_sku = _fresh.sku if _fresh is not None else result.sku

    return SuccessResponse(
        data=ProductResponse(
            id=str(result.id),
            store_id=str(result.store_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            short_description=result.short_description,
            product_type=result.product_type,
            status=result.status,
            price=str(result.price),
            price_currency=result.price_currency,
            compare_at_price=str(result.compare_at_price)
            if result.compare_at_price
            else None,
            cost_price=str(result.cost_price) if result.cost_price else None,
            sku=fresh_sku,
            quantity=fresh_quantity,
            is_in_stock=fresh_quantity > 0,
            is_low_stock=result.is_low_stock,
            is_on_sale=result.is_on_sale,
            category_id=str(result.category_id) if result.category_id else None,
            images=result.images,
            tags=result.tags,
            attributes=result.attributes,
            seo_title=result.seo_title,
            seo_description=result.seo_description,
            template_suffix=result.template_suffix,
            options=(
                [o.model_dump() for o in options_in]
                if options_in is not None
                else (getattr(result, "options", None) or [])
            ),
            variants=variant_summaries or [],
            created_at=str(result.created_at),
            updated_at=str(result.updated_at),
        ),
        message="Product updated successfully",
    )


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete product",
    operation_id="delete_product",
)
async def delete_product(
    product_id: Annotated[UUID, Path(description="Product ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Delete a product."""
    # Step 12 — fetch the slug BEFORE the delete so we can flush the
    # storefront's ISR cache by tag/path post-delete. The row is gone
    # after use_case.execute returns. Best-effort: a missing pre-fetch
    # falls through to the 60s TTL safety net.
    pre_delete_slug: str | None = None
    if store.subdomain:
        try:
            existing = await product_repo.get_by_id(product_id)
            if existing is not None and existing.store_id == store.id:
                pre_delete_slug = existing.slug
        except Exception:  # noqa: BLE001
            pass

    use_case = DeleteProductUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
        event_bus=get_event_bus(),
    )

    await use_case.execute(
        product_id=product_id, user_id=store.owner_id, store_id=store.id
    )

    if store.subdomain and pre_delete_slug:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_product_change,
        )

        await revalidate_on_product_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
            product_slug=pre_delete_slug,
            product_id=str(product_id),
        )

    return None


# =============================================================================
# Image Upload/Delete Endpoints
# =============================================================================


@router.post(
    "/{product_id}/images",
    response_model=SuccessResponse[UploadedImageResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Upload product image",
    operation_id="upload_product_image",
)
async def upload_product_image(
    product_id: Annotated[UUID, Path(description="Product ID")],
    file: Annotated[UploadFile, File(description="Image file to upload")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_cache: Annotated[ProductCacheService, Depends(get_product_cache_service)],
    image_pipeline: Annotated[ImagePipeline, Depends(get_image_pipeline)],
):
    """Upload an image for a product.

    Accepts JPEG, PNG, WebP, and GIF images.
    Maximum file size: 5 MB.

    Images are automatically:
    - Validated and stripped of EXIF metadata
    - Converted to WebP format with 85% quality
    - Resized to 3 variants: thumbnail (150px), medium (600px), large (1200px)
    - Uploaded to Cloudflare R2 storage
    """
    # Validate file size (max 5 MB) and magic bytes before processing
    file_content = await validate_image_upload(file)

    use_case = UploadProductImageUseCase(
        image_pipeline=image_pipeline,
        product_repository=product_repo,
        store_repository=store_repo,
    )

    dto = UploadProductImageDTO(
        product_id=product_id,
        file_content=file_content,
        filename=file.filename or "image",
        content_type=file.content_type or "image/jpeg",
    )

    result = await use_case.execute(
        dto=dto,
        store_id=store.id,
        user_id=store.owner_id,
    )

    # The image use case writes via the repository directly (no domain
    # event), so the ProductCacheInvalidator never fires for it — flush this
    # product's Redis cache (detail + listing pages) so the storefront
    # doesn't keep serving the pre-upload image set.
    await product_cache.invalidate_product(store.id, product_id)

    # Step 12 — flush ISR cache so the new image shows up on PDP / PLP
    # without waiting out the 60s revalidate window. The use case result
    # doesn't carry the slug, so fetch it from the product row.
    # Best-effort: a missing slug fetch falls through to the TTL safety net.
    if store.subdomain:
        product_slug: str | None = None
        try:
            prod = await product_repo.get_by_id(product_id)
            if prod is not None and prod.store_id == store.id:
                product_slug = prod.slug
        except Exception:  # noqa: BLE001
            pass

        if product_slug:
            from src.infrastructure.external_services.nextjs_revalidation import (
                revalidate_on_product_change,
            )

            await revalidate_on_product_change(
                subdomain=store.subdomain,
                store_id=str(store.id),
                product_slug=product_slug,
                product_id=str(product_id),
            )

    return SuccessResponse(
        data=UploadedImageResponse(
            url=result.url,
            key=result.key,
            size=result.size,
            content_type=result.content_type,
            product_id=str(result.product_id),
            variant_urls=result.variant_urls,
        ),
        message="Image uploaded successfully",
    )


@router.delete(
    "/{product_id}/images",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete product image",
    operation_id="delete_product_image",
)
async def delete_product_image(
    product_id: Annotated[UUID, Path(description="Product ID")],
    request: DeleteImageRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_cache: Annotated[ProductCacheService, Depends(get_product_cache_service)],
    storage_service: Annotated[
        CloudflareR2StorageService, Depends(get_storage_service)
    ],
):
    """Delete a product image.

    Removes the image from storage and from the product's image list.
    """
    use_case = DeleteProductImageUseCase(
        storage_service=storage_service,
        product_repository=product_repo,
        store_repository=store_repo,
    )

    await use_case.execute(
        product_id=product_id,
        image_url=request.image_url,
        store_id=store.id,
        user_id=store.owner_id,
    )

    # No domain event on image mutation (see upload_product_image) — flush
    # this product's Redis cache so the deleted image stops being served.
    await product_cache.invalidate_product(store.id, product_id)

    # Step 12 — flush ISR cache so the removed image disappears from
    # PDP / PLP without waiting out the 60s revalidate window. Best-effort:
    # a missing slug fetch falls through to the TTL safety net.
    if store.subdomain:
        product_slug: str | None = None
        try:
            prod = await product_repo.get_by_id(product_id)
            if prod is not None and prod.store_id == store.id:
                product_slug = prod.slug
        except Exception:  # noqa: BLE001
            pass

        if product_slug:
            from src.infrastructure.external_services.nextjs_revalidation import (
                revalidate_on_product_change,
            )

            await revalidate_on_product_change(
                subdomain=store.subdomain,
                store_id=str(store.id),
                product_slug=product_slug,
                product_id=str(product_id),
            )

    return None


# =============================================================================
# CSV Import/Export Endpoints
# =============================================================================


@router.get(
    "/template",
    summary="Download CSV import template",
    operation_id="download_csv_template",
)
async def download_csv_template() -> StreamingResponse:
    """Download an empty CSV template with the correct column headers."""
    import io

    from src.application.use_cases.products.import_products import CSV_COLUMNS

    output = io.StringIO()
    import csv

    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)

    content = output.getvalue()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=products_template.csv"},
    )


@router.post(
    "/import",
    response_model=SuccessResponse[ImportResultResponse],
    summary="Import products from CSV",
    operation_id="import_products",
)
async def import_products(
    file: Annotated[UploadFile, File(description="CSV file to import")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_cache: Annotated[ProductCacheService, Depends(get_product_cache_service)],
):
    """Import products from a CSV file.

    - Creates new products for rows without a matching SKU in the store.
    - Updates existing products when a matching SKU is found.
    - Returns row-level errors without aborting the entire import.
    - Maximum file size: 5 MB.
    """
    # Validate CSV file size (max 10 MB) and content type
    csv_content = await validate_csv_upload(file)

    use_case = ImportProductsUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
    )

    result = await use_case.execute(
        csv_content=csv_content,
        store_id=store.id,
        user_id=store.owner_id,
    )

    # The bulk importer writes products via the repository directly (no
    # per-row domain events), so the ProductCacheInvalidator never fires —
    # sweep the whole store's product cache once so the storefront reflects
    # the imported/updated rows.
    await product_cache.invalidate_store_products(store.id)

    return SuccessResponse(
        data=ImportResultResponse(
            total_rows=result.total_rows,
            created=result.created,
            updated=result.updated,
            errors=[
                ImportRowErrorResponse(row=e.row, field=e.field, message=e.message)
                for e in result.errors
            ],
        ),
        message=f"Import complete: {result.created} created, {result.updated} updated, {len(result.errors)} errors",
    )


@router.get(
    "/export",
    summary="Export products as CSV",
    operation_id="export_products",
)
async def export_products(
    store: Annotated[Store, Depends(verify_store_ownership)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> StreamingResponse:
    """Export all store products as a downloadable CSV file."""
    use_case = ExportProductsUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
    )

    csv_content = await use_case.execute(
        store_id=store.id,
        user_id=store.owner_id,
    )

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="products_{store.id}.csv"',
        },
    )

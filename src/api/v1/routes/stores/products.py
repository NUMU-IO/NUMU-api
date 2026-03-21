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
    ListProductsUseCase,
    UpdateProductUseCase,
    UploadProductImageUseCase,
)
from src.application.use_cases.products.upload_image import UploadProductImageDTO
from src.core.entities.store import Store
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
    use_case = CreateProductUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
        onboarding_repository=onboarding_repo,
        event_bus=get_event_bus(),
    )

    dto = CreateProductDTO(
        name=request.name,
        slug=request.slug,
        sku=request.sku,
        description=request.description,
        short_description=request.short_description,
        product_type=request.product_type,
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
    )

    result = await use_case.execute(
        dto=dto,
        store_id=store.id,
        user_id=store.owner_id,
    )

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
            created_at=str(result.created_at),
            updated_at=str(result.updated_at),
        ),
        message="Product created successfully",
    )


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
    use_case = ListProductsUseCase(product_repository=product_repo)

    # Use the advanced filter path when any extended filter is present
    has_advanced_filters = any([
        sku,
        price_min is not None,
        price_max is not None,
        sort_by,
    ])

    if has_advanced_filters or search:
        skip = (page - 1) * limit
        is_active = None
        if product_status == "active":
            is_active = True
        elif product_status == "draft":
            is_active = False

        items = await product_repo.list_with_filters(
            store_id=store.id,
            category_id=category_id,
            skip=skip,
            limit=limit,
            is_active=is_active,
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
            is_active=is_active,
            search=search,
            sku=sku,
            price_min=price_min,
            price_max=price_max,
        )

        from dataclasses import dataclass

        @dataclass
        class _Result:
            items: list
            total: int

        result = _Result(items=items, total=total)

    elif category_id:
        result = await use_case.by_category(
            category_id=category_id,
            page=page,
            page_size=limit,
        )
    else:
        skip = (page - 1) * limit
        result = await use_case.execute(
            store_id=store.id,
            skip=skip,
            limit=limit,
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

    result = await use_case.execute(product_id=product_id)

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
    )

    result = await use_case.execute(
        product_id=product_id,
        dto=dto,
        user_id=store.owner_id,
    )

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
    use_case = DeleteProductUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
        event_bus=get_event_bus(),
    )

    await use_case.execute(product_id=product_id, user_id=store.owner_id)

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

"""Product routes nested under stores.

URL: /stores/{store_id}/products
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile, status

from src.api.dependencies import (
    get_product_repository,
    get_storage_service,
    get_store_repository,
    require_store_owner,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    CreateProductRequest,
    DeleteImageRequest,
    DeleteResponse,
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
    GetProductUseCase,
    ListProductsUseCase,
    UpdateProductUseCase,
    UploadProductImageUseCase,
)
from src.application.use_cases.products.upload_image import UploadProductImageDTO
from src.infrastructure.external_services.cloudflare_r2 import CloudflareR2StorageService
from src.infrastructure.repositories import ProductRepository, StoreRepository

router = APIRouter(prefix="/{store_id}/products")


@router.post(
    "/",
    response_model=SuccessResponse[ProductResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new product",
)
async def create_product(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CreateProductRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Create a new product for the store."""
    use_case = CreateProductUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
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
        store_id=store_id,
        user_id=user_id,
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
            compare_at_price=str(result.compare_at_price) if result.compare_at_price else None,
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
)
async def list_products(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    category_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    product_status: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
):
    """List products for a store with optional filtering and pagination."""
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
        skip = (page - 1) * limit
        result = await use_case.execute(
            store_id=store_id,
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
            price_currency=product.price_currency,
            compare_at_price=str(product.compare_at_price) if product.compare_at_price else None,
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
)
async def get_product(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
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
            compare_at_price=str(result.compare_at_price) if result.compare_at_price else None,
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
)
async def update_product(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    request: UpdateProductRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update product details."""
    use_case = UpdateProductUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
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
        user_id=user_id,
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
            compare_at_price=str(result.compare_at_price) if result.compare_at_price else None,
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
    response_model=SuccessResponse[DeleteResponse],
    summary="Delete product",
)
async def delete_product(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Delete a product."""
    use_case = DeleteProductUseCase(
        product_repository=product_repo,
        store_repository=store_repo,
    )

    await use_case.execute(product_id=product_id, user_id=user_id)

    return SuccessResponse(
        data=DeleteResponse(deleted=True, id=str(product_id)),
        message="Product deleted successfully",
    )


# =============================================================================
# Image Upload/Delete Endpoints
# =============================================================================


@router.post(
    "/{product_id}/images",
    response_model=SuccessResponse[UploadedImageResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Upload product image",
)
async def upload_product_image(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    file: Annotated[UploadFile, File(description="Image file to upload")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    storage_service: Annotated[CloudflareR2StorageService, Depends(get_storage_service)],
):
    """Upload an image for a product.

    Accepts JPEG, PNG, WebP, and GIF images.
    Maximum file size: 5 MB.
    """
    # Read file content
    file_content = await file.read()

    use_case = UploadProductImageUseCase(
        storage_service=storage_service,
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
        store_id=store_id,
        user_id=user_id,
    )

    return SuccessResponse(
        data=UploadedImageResponse(
            url=result.url,
            key=result.key,
            size=result.size,
            content_type=result.content_type,
            product_id=str(result.product_id),
        ),
        message="Image uploaded successfully",
    )


@router.delete(
    "/{product_id}/images",
    response_model=SuccessResponse[DeleteResponse],
    summary="Delete product image",
)
async def delete_product_image(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    request: DeleteImageRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    storage_service: Annotated[CloudflareR2StorageService, Depends(get_storage_service)],
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
        store_id=store_id,
        user_id=user_id,
    )

    return SuccessResponse(
        data=DeleteResponse(deleted=True, id=str(product_id)),
        message="Image deleted successfully",
    )

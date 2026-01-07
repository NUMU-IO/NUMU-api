"""Product routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.api.dependencies import (
    get_current_user_id,
    get_product_repository,
    require_store_owner,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    CreateProductRequest,
    DeleteResponse,
    PaginatedListResponse,
    ProductResponse,
    UpdateProductRequest,
)
from src.application.use_cases.products import (
    CreateProductUseCase,
    DeleteProductUseCase,
    GetProductUseCase,
    ListProductsUseCase,
    UpdateProductUseCase,
)
from src.infrastructure.repositories import ProductRepository

router = APIRouter(prefix="/products", tags=["Products"])


@router.post(
    "",
    response_model=SuccessResponse[ProductResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new product",
)
async def create_product(
    request: CreateProductRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Create a new product."""
    use_case = CreateProductUseCase(product_repository=product_repo)
    
    result = await use_case.execute(
        store_id=request.store_id,
        name=request.name,
        description=request.description,
        price=request.price,
        currency=request.currency,
        sku=request.sku,
        barcode=request.barcode,
        quantity=request.quantity,
        category_id=request.category_id,
        images=request.images,
        is_active=request.is_active,
    )
    
    return SuccessResponse(
        data=ProductResponse(
            id=str(result.id),
            store_id=str(result.store_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            price=result.price,
            currency=result.currency,
            compare_at_price=result.compare_at_price,
            sku=result.sku,
            barcode=result.barcode,
            quantity=result.quantity,
            category_id=str(result.category_id) if result.category_id else None,
            images=result.images,
            is_active=result.is_active,
            is_featured=result.is_featured,
            created_at=result.created_at,
            updated_at=result.updated_at,
        ),
        message="Product created successfully",
    )


@router.get(
    "",
    response_model=SuccessResponse[PaginatedListResponse[ProductResponse]],
    summary="List products",
)
async def list_products(
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_id: UUID | None = Query(None),
    category_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    is_active: bool | None = Query(None),
    search: str | None = Query(None),
):
    """List products with optional filtering and pagination."""
    use_case = ListProductsUseCase(product_repository=product_repo)
    
    result = await use_case.execute(
        store_id=store_id,
        category_id=category_id,
        skip=(page - 1) * limit,
        limit=limit,
        is_active=is_active,
        search=search,
    )
    
    products = [
        ProductResponse(
            id=str(product.id),
            store_id=str(product.store_id),
            name=product.name,
            slug=product.slug,
            description=product.description,
            price=product.price,
            currency=product.currency,
            compare_at_price=product.compare_at_price,
            sku=product.sku,
            barcode=product.barcode,
            quantity=product.quantity,
            category_id=str(product.category_id) if product.category_id else None,
            images=product.images,
            is_active=product.is_active,
            is_featured=product.is_featured,
            created_at=product.created_at,
            updated_at=product.updated_at,
        )
        for product in result.products
    ]
    
    return SuccessResponse(
        data=PaginatedListResponse(
            items=products,
            total=result.total,
            page=page,
            limit=limit,
            pages=(result.total + limit - 1) // limit,
        ),
        message="Products retrieved successfully",
    )


@router.get(
    "/{product_id}",
    response_model=SuccessResponse[ProductResponse],
    summary="Get product by ID",
)
async def get_product(
    product_id: UUID,
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
            price=result.price,
            currency=result.currency,
            compare_at_price=result.compare_at_price,
            sku=result.sku,
            barcode=result.barcode,
            quantity=result.quantity,
            category_id=str(result.category_id) if result.category_id else None,
            images=result.images,
            is_active=result.is_active,
            is_featured=result.is_featured,
            created_at=result.created_at,
            updated_at=result.updated_at,
        ),
        message="Product retrieved successfully",
    )


@router.patch(
    "/{product_id}",
    response_model=SuccessResponse[ProductResponse],
    summary="Update product",
)
async def update_product(
    product_id: UUID,
    request: UpdateProductRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Update product details."""
    use_case = UpdateProductUseCase(product_repository=product_repo)
    
    result = await use_case.execute(
        product_id=product_id,
        name=request.name,
        description=request.description,
        price=request.price,
        compare_at_price=request.compare_at_price,
        sku=request.sku,
        barcode=request.barcode,
        quantity=request.quantity,
        category_id=request.category_id,
        images=request.images,
        is_active=request.is_active,
        is_featured=request.is_featured,
    )
    
    return SuccessResponse(
        data=ProductResponse(
            id=str(result.id),
            store_id=str(result.store_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            price=result.price,
            currency=result.currency,
            compare_at_price=result.compare_at_price,
            sku=result.sku,
            barcode=result.barcode,
            quantity=result.quantity,
            category_id=str(result.category_id) if result.category_id else None,
            images=result.images,
            is_active=result.is_active,
            is_featured=result.is_featured,
            created_at=result.created_at,
            updated_at=result.updated_at,
        ),
        message="Product updated successfully",
    )


@router.delete(
    "/{product_id}",
    response_model=SuccessResponse[DeleteResponse],
    summary="Delete product",
)
async def delete_product(
    product_id: UUID,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Delete a product."""
    use_case = DeleteProductUseCase(product_repository=product_repo)
    
    await use_case.execute(product_id=product_id)
    
    return SuccessResponse(
        data=DeleteResponse(deleted=True, id=str(product_id)),
        message="Product deleted successfully",
    )

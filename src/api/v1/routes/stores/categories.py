"""Category routes nested under stores.

URL: /stores/{store_id}/categories
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile, status

from src.api.dependencies import (
    get_category_repository,
    get_image_pipeline,
    get_store_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_image_upload
from src.api.v1.schemas import (
    CategoryResponse,
    CreateCategoryRequest,
    UpdateCategoryRequest,
)
from src.application.dto.category import CreateCategoryDTO, UpdateCategoryDTO
from src.application.use_cases.categories import (
    CreateCategoryUseCase,
    DeleteCategoryUseCase,
    GetCategoryUseCase,
    ListCategoriesUseCase,
    UpdateCategoryUseCase,
)
from src.core.entities.store import Store
from src.core.interfaces.services.storage_service import StorageBucket
from src.infrastructure.external_services.image import ImagePipeline
from src.infrastructure.repositories import CategoryRepository, StoreRepository

router = APIRouter(prefix="/{store_id}/categories")


def _category_response(result) -> CategoryResponse:
    """Convert CategoryDTO to CategoryResponse."""
    return CategoryResponse(
        id=str(result.id),
        store_id=str(result.store_id),
        name=result.name,
        slug=result.slug,
        description=result.description,
        image_url=result.image_url,
        parent_id=str(result.parent_id) if result.parent_id else None,
        position=result.position,
        is_active=result.is_active,
        product_count=result.product_count,
        extra_data=result.metadata if result.metadata else None,
        created_at=str(result.created_at),
        updated_at=str(result.updated_at),
    )


@router.post(
    "/",
    response_model=SuccessResponse[CategoryResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new category",
    operation_id="create_category",
)
async def create_category(
    request: CreateCategoryRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Create a new category for the store."""
    use_case = CreateCategoryUseCase(
        category_repository=category_repo,
        store_repository=store_repo,
    )

    dto = CreateCategoryDTO(
        name=request.name,
        slug=request.slug,
        description=request.description,
        image_url=request.image_url,
        parent_id=UUID(request.parent_id) if request.parent_id else None,
        position=request.position,
        is_active=request.is_active,
        extra_data=request.extra_data,
    )

    result = await use_case.execute(dto=dto, store_id=store.id, user_id=store.owner_id)

    # Step 12 — flush ISR cache so new category shows up on home + PLP
    # without waiting out the 60s revalidate window. Best-effort.
    if store.subdomain:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_category_change,
        )

        await revalidate_on_category_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
        )

    return SuccessResponse(
        data=_category_response(result),
        message="Category created successfully",
    )


@router.get(
    "/",
    response_model=SuccessResponse[list[CategoryResponse]],
    summary="List categories",
    operation_id="list_categories",
)
async def list_categories(
    store: Annotated[Store, Depends(verify_store_ownership)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    include_inactive: bool = Query(False),
):
    """List categories for a store."""
    use_case = ListCategoriesUseCase(category_repository=category_repo)

    results = await use_case.execute(
        store_id=store.id,
        include_inactive=include_inactive,
    )

    return SuccessResponse(
        data=[_category_response(c) for c in results],
        message="Categories retrieved successfully",
    )


@router.get(
    "/{category_id}",
    response_model=SuccessResponse[CategoryResponse],
    summary="Get category by ID",
    operation_id="get_category",
)
async def get_category(
    category_id: Annotated[UUID, Path(description="Category ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
):
    """Get category details by ID."""
    use_case = GetCategoryUseCase(category_repository=category_repo)
    result = await use_case.execute(category_id=category_id)

    return SuccessResponse(
        data=_category_response(result),
        message="Category retrieved successfully",
    )


@router.patch(
    "/{category_id}",
    response_model=SuccessResponse[CategoryResponse],
    summary="Update category",
    operation_id="update_category",
)
async def update_category(
    category_id: Annotated[UUID, Path(description="Category ID")],
    request: UpdateCategoryRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update category details."""
    use_case = UpdateCategoryUseCase(
        category_repository=category_repo,
        store_repository=store_repo,
    )

    dto = UpdateCategoryDTO(
        name=request.name,
        slug=request.slug,
        description=request.description,
        image_url=request.image_url,
        parent_id=UUID(request.parent_id) if request.parent_id else None,
        position=request.position,
        is_active=request.is_active,
        extra_data=request.extra_data,
    )

    result = await use_case.execute(
        category_id=category_id, dto=dto, user_id=store.owner_id
    )

    # Step 12 — flush ISR cache after rename / image / parent change.
    # Best-effort.
    if store.subdomain:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_category_change,
        )

        await revalidate_on_category_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
        )

    return SuccessResponse(
        data=_category_response(result),
        message="Category updated successfully",
    )


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete category",
    operation_id="delete_category",
)
async def delete_category(
    category_id: Annotated[UUID, Path(description="Category ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Delete a category."""
    use_case = DeleteCategoryUseCase(
        category_repository=category_repo,
        store_repository=store_repo,
    )

    await use_case.execute(category_id=category_id, user_id=store.owner_id)

    # Step 12 — flush ISR cache so the deleted category disappears from
    # the storefront without waiting out the 60s revalidate window.
    # Best-effort.
    if store.subdomain:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_category_change,
        )

        await revalidate_on_category_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
        )

    return None


# =============================================================================
# Category Image Upload
# =============================================================================


@router.post(
    "/{category_id}/image",
    response_model=SuccessResponse[CategoryResponse],
    status_code=status.HTTP_200_OK,
    summary="Upload category image",
    operation_id="upload_category_image",
)
async def upload_category_image(
    category_id: Annotated[UUID, Path(description="Category ID")],
    file: Annotated[UploadFile, File(description="Image file to upload")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    category_repo: Annotated[CategoryRepository, Depends(get_category_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    image_pipeline: Annotated[ImagePipeline, Depends(get_image_pipeline)],
):
    """Upload an image for a category.

    Accepts JPEG, PNG, WebP images. Maximum file size: 5 MB.
    The image is processed and the category's image_url is updated.
    """
    file_content = await validate_image_upload(file)

    # Process and upload the image
    result = await image_pipeline.process_and_upload(
        file_data=file_content,
        product_id=category_id,
        original_filename=file.filename or "category-image",
        bucket=StorageBucket.CATEGORIES,
    )

    # Update the category's image_url
    use_case = UpdateCategoryUseCase(
        category_repository=category_repo,
        store_repository=store_repo,
    )
    dto = UpdateCategoryDTO(image_url=result.url)
    updated = await use_case.execute(
        category_id=category_id, dto=dto, user_id=store.owner_id
    )

    # Step 12 — flush ISR cache so the new image appears on home + PLP
    # without waiting out the 60s revalidate window. Best-effort.
    if store.subdomain:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_category_change,
        )

        await revalidate_on_category_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
        )

    return SuccessResponse(
        data=_category_response(updated),
        message="Category image uploaded successfully",
    )

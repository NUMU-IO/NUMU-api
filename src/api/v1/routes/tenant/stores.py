"""Store routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.api.dependencies import (
    get_current_user_id,
    get_store_repository,
    require_store_owner,
)
from src.api.responses import PaginatedResponse, SuccessResponse
from src.api.v1.schemas import (
    CreateStoreRequest,
    DeleteResponse,
    PaginatedListResponse,
    StoreResponse,
    UpdateStoreRequest,
)
from src.application.use_cases.stores import (
    CreateStoreUseCase,
    DeleteStoreUseCase,
    GetStoreUseCase,
    ListStoresUseCase,
    UpdateStoreUseCase,
)
from src.infrastructure.repositories import StoreRepository

router = APIRouter(prefix="/stores", tags=["Stores"])


@router.post(
    "",
    response_model=SuccessResponse[StoreResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new store",
)
async def create_store(
    request: CreateStoreRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Create a new store."""
    use_case = CreateStoreUseCase(store_repository=store_repo)
    
    result = await use_case.execute(
        owner_id=user_id,
        name=request.name,
        description=request.description,
        logo_url=request.logo_url,
        website=request.website,
        email=request.email,
        phone=request.phone,
        currency=request.currency,
        country=request.country,
    )
    
    return SuccessResponse(
        data=StoreResponse(
            id=str(result.id),
            owner_id=str(result.owner_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            logo_url=result.logo_url,
            website=result.website,
            email=result.email,
            phone=result.phone,
            currency=result.currency,
            country=result.country,
            is_active=result.is_active,
            is_verified=result.is_verified,
            created_at=result.created_at,
            updated_at=result.updated_at,
        ),
        message="Store created successfully",
    )


@router.get(
    "",
    response_model=SuccessResponse[PaginatedListResponse[StoreResponse]],
    summary="List stores",
)
async def list_stores(
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    is_active: bool | None = Query(None),
):
    """List all stores with pagination."""
    use_case = ListStoresUseCase(store_repository=store_repo)
    
    result = await use_case.execute(
        skip=(page - 1) * limit,
        limit=limit,
        is_active=is_active,
    )
    
    stores = [
        StoreResponse(
            id=str(store.id),
            owner_id=str(store.owner_id),
            name=store.name,
            slug=store.slug,
            description=store.description,
            logo_url=store.logo_url,
            website=store.website,
            email=store.email,
            phone=store.phone,
            currency=store.currency,
            country=store.country,
            is_active=store.is_active,
            is_verified=store.is_verified,
            created_at=store.created_at,
            updated_at=store.updated_at,
        )
        for store in result.stores
    ]
    
    return SuccessResponse(
        data=PaginatedListResponse(
            items=stores,
            total=result.total,
            page=page,
            limit=limit,
            pages=(result.total + limit - 1) // limit,
        ),
        message="Stores retrieved successfully",
    )


@router.get(
    "/{store_id}",
    response_model=SuccessResponse[StoreResponse],
    summary="Get store by ID",
)
async def get_store(
    store_id: UUID,
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Get store details by ID."""
    use_case = GetStoreUseCase(store_repository=store_repo)
    
    result = await use_case.execute(store_id=store_id)
    
    return SuccessResponse(
        data=StoreResponse(
            id=str(result.id),
            owner_id=str(result.owner_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            logo_url=result.logo_url,
            website=result.website,
            email=result.email,
            phone=result.phone,
            currency=result.currency,
            country=result.country,
            is_active=result.is_active,
            is_verified=result.is_verified,
            created_at=result.created_at,
            updated_at=result.updated_at,
        ),
        message="Store retrieved successfully",
    )


@router.patch(
    "/{store_id}",
    response_model=SuccessResponse[StoreResponse],
    summary="Update store",
)
async def update_store(
    store_id: UUID,
    request: UpdateStoreRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update store details."""
    use_case = UpdateStoreUseCase(store_repository=store_repo)
    
    result = await use_case.execute(
        store_id=store_id,
        owner_id=user_id,
        name=request.name,
        description=request.description,
        logo_url=request.logo_url,
        website=request.website,
        email=request.email,
        phone=request.phone,
    )
    
    return SuccessResponse(
        data=StoreResponse(
            id=str(result.id),
            owner_id=str(result.owner_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            logo_url=result.logo_url,
            website=result.website,
            email=result.email,
            phone=result.phone,
            currency=result.currency,
            country=result.country,
            is_active=result.is_active,
            is_verified=result.is_verified,
            created_at=result.created_at,
            updated_at=result.updated_at,
        ),
        message="Store updated successfully",
    )


@router.delete(
    "/{store_id}",
    response_model=SuccessResponse[DeleteResponse],
    summary="Delete store",
)
async def delete_store(
    store_id: UUID,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Delete a store."""
    use_case = DeleteStoreUseCase(store_repository=store_repo)
    
    await use_case.execute(store_id=store_id, owner_id=user_id)
    
    return SuccessResponse(
        data=DeleteResponse(deleted=True, id=str(store_id)),
        message="Store deleted successfully",
    )

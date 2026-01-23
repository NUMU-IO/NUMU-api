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
from src.application.dto.store import CreateStoreDTO, UpdateStoreDTO
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
    
    dto = CreateStoreDTO(
        name=request.name,
        slug=request.slug,
        description=request.description,
        default_currency=request.default_currency,
        contact_email=request.contact_email,
        contact_phone=request.contact_phone,
    )
    
    result = await use_case.execute(dto, owner_id=user_id)
    
    return SuccessResponse(
        data=StoreResponse(
            id=str(result.id),
            owner_id=str(result.owner_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            logo_url=result.logo_url,
            banner_url=result.banner_url,
            status=result.status,
            default_currency=result.default_currency,
            contact_email=result.contact_email,
            contact_phone=result.contact_phone,
            address=result.address,
            social_links=result.social_links,
            created_at=str(result.created_at),
            updated_at=str(result.updated_at),
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
        page=page,
        page_size=limit,
    )
    
    stores = [
        StoreResponse(
            id=str(store.id),
            owner_id=str(store.owner_id),
            name=store.name,
            slug=store.slug,
            description=store.description,
            logo_url=store.logo_url,
            banner_url=store.banner_url,
            status=store.status,
            default_currency=store.default_currency,
            contact_email=store.contact_email,
            contact_phone=store.contact_phone,
            address=store.address,
            social_links=store.social_links,
            created_at=str(store.created_at),
            updated_at=str(store.updated_at),
        )
        for store in result.items
    ]
    
    return SuccessResponse(
        data=PaginatedListResponse(
            items=stores,
            total=result.total,
            page=page,
            page_size=limit,
            total_pages=(result.total + limit - 1) // limit,
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
            banner_url=result.banner_url,
            status=result.status,
            default_currency=result.default_currency,
            contact_email=result.contact_email,
            contact_phone=result.contact_phone,
            address=result.address,
            social_links=result.social_links,
            created_at=str(result.created_at),
            updated_at=str(result.updated_at),
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
    
    dto = UpdateStoreDTO(
        name=request.name,
        description=request.description,
        logo_url=request.logo_url,
        banner_url=request.banner_url,
        contact_email=request.contact_email,
        contact_phone=request.contact_phone,
        address=request.address,
        social_links=request.social_links,
        settings=request.settings,
    )
    
    result = await use_case.execute(
        store_id=store_id,
        dto=dto,
        user_id=user_id,
    )
    
    return SuccessResponse(
        data=StoreResponse(
            id=str(result.id),
            owner_id=str(result.owner_id),
            name=result.name,
            slug=result.slug,
            description=result.description,
            logo_url=result.logo_url,
            banner_url=result.banner_url,
            status=result.status,
            default_currency=result.default_currency,
            contact_email=result.contact_email,
            contact_phone=result.contact_phone,
            address=result.address,
            social_links=result.social_links,
            created_at=str(result.created_at),
            updated_at=str(result.updated_at),
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
    
    await use_case.execute(store_id=store_id, user_id=user_id)
    
    return SuccessResponse(
        data=DeleteResponse(deleted=True, id=str(store_id)),
        message="Store deleted successfully",
    )

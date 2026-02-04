"""Store CRUD routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_store_repository,
    require_store_owner,
)
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    CreateStoreRequest,
    DeleteResponse,
    PaginatedListResponse,
    StoreResponse,
    UpdateStoreRequest,
)
from src.api.v1.schemas.tenant.store import (
    CheckSubdomainRequest,
    CheckSubdomainResponse,
)
from src.application.dto.store import CreateStoreDTO, UpdateStoreDTO
from src.application.use_cases.stores import (
    CreateStoreUseCase,
    DeleteStoreUseCase,
    GetStoreUseCase,
    ListStoresUseCase,
    UpdateStoreUseCase,
)
from src.application.use_cases.stores.create_store import (
    RESERVED_SUBDOMAINS,
    validate_subdomain,
)
from src.infrastructure.repositories import StoreRepository
from src.infrastructure.tenancy.service import TenantService

router = APIRouter()


def _build_store_response(store) -> StoreResponse:
    """Build StoreResponse from store DTO."""
    return StoreResponse(
        id=str(store.id),
        owner_id=str(store.owner_id),
        name=store.name,
        slug=store.slug,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
        store_url=store.store_url,
        description=store.description,
        logo_url=store.logo_url,
        banner_url=store.banner_url,
        status=store.status,
        default_currency=store.default_currency,
        contact_email=store.contact_email,
        contact_phone=store.contact_phone,
        address=store.address,
        social_links=store.social_links,
        theme_settings=store.theme_settings,
        created_at=str(store.created_at),
        updated_at=str(store.updated_at),
    )


@router.post(
    "/check-subdomain",
    response_model=SuccessResponse[CheckSubdomainResponse],
    summary="Check subdomain availability",
)
async def check_subdomain(
    request: CheckSubdomainRequest,
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Check if a subdomain is available for use."""
    subdomain = request.subdomain.lower().strip()

    # Check reserved
    if subdomain in RESERVED_SUBDOMAINS:
        return SuccessResponse(
            data=CheckSubdomainResponse(
                subdomain=subdomain,
                available=False,
                message=f"'{subdomain}' is a reserved subdomain",
            ),
            message="Subdomain check completed",
        )

    # Validate format
    try:
        validate_subdomain(subdomain)
    except Exception as e:
        return SuccessResponse(
            data=CheckSubdomainResponse(
                subdomain=subdomain,
                available=False,
                message=str(e),
            ),
            message="Subdomain check completed",
        )

    # Check if exists
    exists = await store_repo.subdomain_exists(subdomain)

    return SuccessResponse(
        data=CheckSubdomainResponse(
            subdomain=subdomain,
            available=not exists,
            message="Subdomain is already taken"
            if exists
            else "Subdomain is available",
        ),
        message="Subdomain check completed",
    )


@router.post(
    "/",
    response_model=SuccessResponse[StoreResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new store",
)
async def create_store(
    request: CreateStoreRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new store with a subdomain."""
    store_repo = StoreRepository(db)
    tenant_service = TenantService(db)
    use_case = CreateStoreUseCase(
        store_repository=store_repo, tenant_service=tenant_service
    )

    dto = CreateStoreDTO(
        name=request.name,
        subdomain=request.subdomain,
        slug=request.slug,
        description=request.description,
        default_currency=request.default_currency,
        contact_email=request.contact_email,
        contact_phone=request.contact_phone,
    )

    result = await use_case.execute(dto, owner_id=user_id)

    return SuccessResponse(
        data=_build_store_response(result),
        message="Store created successfully",
    )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[StoreResponse]],
    summary="List my stores",
)
async def list_stores(
    user_id: Annotated[UUID, Depends(require_store_owner)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List stores owned by the current user."""
    use_case = ListStoresUseCase(store_repository=store_repo)

    result = await use_case.by_owner(
        owner_id=user_id,
        page=page,
        page_size=limit,
    )

    stores = [_build_store_response(store) for store in result.items]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=stores,
            total=result.total,
            page=page,
            page_size=limit,
            total_pages=(result.total + limit - 1) // limit if limit > 0 else 0,
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
    user_id: Annotated[UUID, Depends(require_store_owner)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Get store details by ID. Only accessible by the store owner."""
    use_case = GetStoreUseCase(store_repository=store_repo)

    result = await use_case.execute(store_id=store_id)

    # Verify ownership
    if result.owner_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this store",
        )

    return SuccessResponse(
        data=_build_store_response(result),
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
        theme_settings=request.theme_settings,
    )

    result = await use_case.execute(
        store_id=store_id,
        dto=dto,
        user_id=user_id,
    )

    return SuccessResponse(
        data=_build_store_response(result),
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

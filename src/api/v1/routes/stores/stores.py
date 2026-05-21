"""Store CRUD routes."""

from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_current_user_id,
    get_store_repository,
    get_storefront_cache_service,
    require_store_owner,
    verify_store_ownership,
)
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import get_onboarding_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    CreateStoreRequest,
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
    ListStoresUseCase,
    UpdateStoreUseCase,
)
from src.application.use_cases.stores.create_store import (
    RESERVED_SUBDOMAINS,
    validate_subdomain,
)
from src.core.entities.store import Store
from src.infrastructure.cache import StorefrontCache
from src.infrastructure.external_services.cloudflare import cloudflare_dns_service
from src.infrastructure.repositories import OnboardingRepository, StoreRepository
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
        default_language=store.default_language,
        contact_email=store.contact_email,
        contact_phone=store.contact_phone,
        address=store.address,
        social_links=store.social_links,
        settings=getattr(store, "settings", None) or {},
        theme_settings=store.theme_settings,
        business_hours=getattr(store, "business_hours", None) or {},
        created_at=str(store.created_at),
        updated_at=str(store.updated_at),
    )


@router.post(
    "/check-subdomain",
    response_model=SuccessResponse[CheckSubdomainResponse],
    summary="Check subdomain availability",
    operation_id="check_subdomain",
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
    operation_id="create_store",
)
async def create_store(
    request: CreateStoreRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new store with a subdomain."""
    from datetime import datetime

    from sqlalchemy import select

    from src.infrastructure.database.models.public.user import UserModel

    # Determine plan based on user's trial status
    user_result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = user_result.scalar_one_or_none()
    plan = (
        "demo"
        if user and user.trial_ends_at and user.trial_ends_at > datetime.now(UTC)
        else "free"
    )

    store_repo = StoreRepository(db)
    onboarding_repo = OnboardingRepository(db)
    tenant_service = TenantService(db)
    use_case = CreateStoreUseCase(
        store_repository=store_repo,
        tenant_service=tenant_service,
        onboarding_repository=onboarding_repo,
    )

    dto = CreateStoreDTO(
        name=request.name,
        subdomain=request.subdomain,
        slug=request.slug,
        description=request.description,
        default_currency=request.default_currency,
        default_language=request.default_language,
        contact_email=request.contact_email,
        contact_phone=request.contact_phone,
    )

    result = await use_case.execute(dto, owner_id=user_id, plan=plan)

    if result.subdomain:
        await cloudflare_dns_service.ensure_store_subdomain(result.subdomain)

    return SuccessResponse(
        data=_build_store_response(result),
        message="Store created successfully",
    )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[StoreResponse]],
    summary="List my stores",
    operation_id="list_stores",
)
async def list_stores(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List stores the current user can access.

    Returns stores the user owns outright and stores the user is an active
    tenant member of (e.g. accepted a staff invitation). This is what the
    merchant dashboard uses to decide whether to show the store picker or
    redirect to /create-store.
    """
    use_case = ListStoresUseCase(store_repository=store_repo)

    result = await use_case.accessible_for_user(
        user_id=user_id,
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
    operation_id="get_store",
)
async def get_store(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Get store details by ID. Only accessible by the store owner."""
    return SuccessResponse(
        data=_build_store_response(store),
        message="Store retrieved successfully",
    )


@router.patch(
    "/{store_id}",
    response_model=SuccessResponse[StoreResponse],
    summary="Update store",
    operation_id="update_store",
)
async def update_store(
    request: UpdateStoreRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Update store details."""
    use_case = UpdateStoreUseCase(
        store_repository=store_repo,
        onboarding_repository=onboarding_repo,
    )

    dto = UpdateStoreDTO(
        name=request.name,
        description=request.description,
        logo_url=request.logo_url,
        banner_url=request.banner_url,
        contact_email=request.contact_email,
        contact_phone=request.contact_phone,
        address=request.address,
        social_links=request.social_links,
        default_language=request.default_language,
        status=request.status,
        settings=request.settings,
        theme_settings=request.theme_settings,
        business_hours=request.business_hours,
    )

    result = await use_case.execute(
        store_id=store.id,
        dto=dto,
        user_id=store.owner_id,
    )

    await cache.invalidate_store(
        store_id=result.id,
        subdomain=result.subdomain,
        custom_domain=result.custom_domain,
    )
    if request.theme_settings is not None:
        await cache.invalidate_theme(result.id)

    return SuccessResponse(
        data=_build_store_response(result),
        message="Store updated successfully",
    )


@router.delete(
    "/{store_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete store",
    operation_id="delete_store",
)
async def delete_store(
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Delete a store."""
    use_case = DeleteStoreUseCase(store_repository=store_repo)

    await use_case.execute(store_id=store.id, user_id=store.owner_id)

    await cache.invalidate_store(
        store_id=store.id,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
    )
    await cache.invalidate_theme(store.id)

    return None


# ─── Phase 5.11 — demo seed catalog ───────────────────────────────


@router.post(
    "/{store_id}/seed-demo",
    summary="Seed demo catalog (5 products + 1 collection)",
    operation_id="seed_demo_catalog",
)
async def seed_demo_catalog_route(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Phase 5.11 — opt-in demo seed.

    Inserts 5 placeholder products + 1 starter collection so the
    merchant can preview their storefront before uploading their own
    catalog. Idempotent — re-running the seed against a store that
    already has demo rows is a no-op (slug uniqueness covers it).

    The merchant calls this from the hub onboarding flow when they
    pick "Try with demo products" on store creation, OR later via
    Settings → Demo Catalog → "Refresh demo data".
    """
    from src.application.services.demo_seed_service import seed_demo_catalog

    counts = await seed_demo_catalog(
        store_id=store.id,
        tenant_id=store.tenant_id or store.id,
    )
    return {"seeded": True, **counts}


@router.delete(
    "/{store_id}/seed-demo",
    summary="Remove demo catalog (bulk delete tagged products)",
    operation_id="remove_demo_catalog",
)
async def remove_demo_catalog_route(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Phase 5.11 — bulk delete demo-tagged products.

    Used by the hub's "Reset demo" / "I'm ready to go live" button
    — merchants who started seeded but want a clean slate before
    launch run this. Doesn't touch products without the `demo` tag,
    so a merchant who edited a seeded product's tags off keeps that
    product (intentional — once they edit it, it's "real").
    """
    from src.application.services.demo_seed_service import remove_demo_catalog

    deleted = await remove_demo_catalog(store_id=store.id)
    return {"deleted": deleted}

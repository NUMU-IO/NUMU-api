"""Store installation routes for marketplace themes.

All endpoints are scoped to a `store_id` path param and gated by
`verify_store_ownership` so a merchant can only manage installs for
stores they own.

Mounted at `/stores/{store_id}/marketplace/...` from routes/__init__.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import (
    get_current_user_id,
    get_store_repository,
    verify_store_ownership,
)
from src.api.dependencies.repositories import (
    get_marketplace_repository,
    get_store_theme_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    ActivateThemeRequest,
    InstallationResponse,
    InstalledListResponse,
    InstallThemeRequest,
)
from src.application.services.marketplace_service import MarketplaceService
from src.infrastructure.repositories import (
    MarketplaceRepository,
    StoreRepository,
    StoreThemeRepository,
)

router = APIRouter(
    prefix="/marketplace",
    tags=["Marketplace Store Install"],
    dependencies=[Depends(verify_store_ownership)],
)


def _svc(
    marketplace_repo: Annotated[
        MarketplaceRepository, Depends(get_marketplace_repository)
    ],
    store_theme_repo: Annotated[
        StoreThemeRepository, Depends(get_store_theme_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> MarketplaceService:
    return MarketplaceService(
        marketplace_repo=marketplace_repo,
        store_theme_repo=store_theme_repo,
        store_repo=store_repo,
    )


@router.get("/installed", response_model=SuccessResponse[InstalledListResponse])
async def list_installed(
    store_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """List marketplace themes installed on a store."""
    items = await svc.list_installed(store_id)
    return SuccessResponse(data=InstalledListResponse(installed=items))


@router.post(
    "/install",
    response_model=SuccessResponse[InstallationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def install_theme(
    store_id: UUID,
    body: InstallThemeRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    _user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Install (or reinstall) the latest published version of a theme.

    Installation does NOT activate the theme — call `/activate` after
    installing. This makes upgrades reversible.
    """
    try:
        data = await svc.install_theme(
            store_id=store_id,
            marketplace_theme_id=UUID(body.marketplace_theme_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=InstallationResponse(**data))


@router.post(
    "/activate",
    response_model=SuccessResponse[InstallationResponse],
)
async def activate_theme(
    store_id: UUID,
    body: ActivateThemeRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Activate an installed marketplace theme.

    Side effects: marks this install active (deactivating any other
    marketplace install for the store), seeds the V3 draft from the
    version's presets, and triggers Next.js cache invalidation.
    """
    try:
        data = await svc.activate_theme(
            store_id=store_id,
            marketplace_theme_id=UUID(body.marketplace_theme_id),
            user_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=InstallationResponse(**data))


@router.delete("/uninstall/{theme_id}")
async def uninstall_theme(
    store_id: UUID,
    theme_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """Uninstall a marketplace theme from a store."""
    ok = await svc.uninstall_theme(store_id, theme_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="theme is not installed on this store",
        )
    return SuccessResponse(
        data={"uninstalled": True, "marketplace_theme_id": str(theme_id)},
        message="Theme uninstalled",
    )

"""Developer marketplace routes for theme submission.

All routes require an authenticated user (the developer). Listings are
scoped to the caller — the service layer enforces ownership on every
read and write.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import get_current_user_id
from src.api.dependencies.repositories import get_marketplace_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    CreateListingRequest,
    MarketplaceThemeOut,
    SubmitVersionRequest,
    UpdateListingRequest,
    VersionStatusResponse,
    VersionSummaryOut,
)
from src.application.services.marketplace_service import MarketplaceService
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

router = APIRouter(
    prefix="/marketplace/developer",
    tags=["Marketplace Developer"],
    # Every route below requires an authenticated caller. The service
    # layer additionally enforces ownership of the referenced theme.
    dependencies=[Depends(get_current_user_id)],
)


def _svc(
    repo: Annotated[MarketplaceRepository, Depends(get_marketplace_repository)],
) -> MarketplaceService:
    return MarketplaceService(marketplace_repo=repo)


@router.post(
    "/themes",
    response_model=SuccessResponse[MarketplaceThemeOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_listing(
    body: CreateListingRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Create a new marketplace theme listing (status=draft)."""
    try:
        data = await svc.create_listing(developer_id=user_id, data=body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=MarketplaceThemeOut(**data))


@router.get(
    "/themes",
    response_model=SuccessResponse[list[MarketplaceThemeOut]],
)
async def list_my_themes(
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """List theme listings owned by the authenticated developer."""
    items = await svc.list_my_themes(user_id)
    return SuccessResponse(data=[MarketplaceThemeOut(**i) for i in items])


@router.patch(
    "/themes/{theme_id}",
    response_model=SuccessResponse[MarketplaceThemeOut],
)
async def update_listing(
    theme_id: UUID,
    body: UpdateListingRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Update a marketplace theme listing (developer-owned only)."""
    fields = body.model_dump(exclude_unset=True)
    try:
        data = await svc.update_listing(
            developer_id=user_id, theme_id=theme_id, fields=fields
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=MarketplaceThemeOut(**data))


@router.post(
    "/themes/{theme_id}/versions",
    response_model=SuccessResponse[VersionStatusResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_version(
    theme_id: UUID,
    body: SubmitVersionRequest,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Submit a new version for build & review.

    Pre-requisite: upload the theme ZIP via `POST /api/v1/themes/upload`.
    Pass the resulting on-disk path as `source_zip_path`.
    """
    try:
        data = await svc.submit_version(
            developer_id=user_id,
            theme_id=theme_id,
            version_string=body.version_string,
            source_zip_path=body.source_zip_path,
            release_notes=body.release_notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return SuccessResponse(data=VersionStatusResponse(**data))


@router.get(
    "/themes/{theme_id}/versions",
    response_model=SuccessResponse[list[VersionSummaryOut]],
)
async def list_versions(
    theme_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """List all versions of a theme owned by the developer."""
    try:
        data = await svc.list_versions(developer_id=user_id, theme_id=theme_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=[VersionSummaryOut(**v) for v in data])


@router.get(
    "/versions/{version_id}/status",
    response_model=SuccessResponse[VersionStatusResponse],
)
async def check_build_status(
    version_id: UUID,
    svc: Annotated[MarketplaceService, Depends(_svc)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Poll the build/review status of a submitted version."""
    try:
        data = await svc.get_version_status(developer_id=user_id, version_id=version_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=VersionStatusResponse(**data))

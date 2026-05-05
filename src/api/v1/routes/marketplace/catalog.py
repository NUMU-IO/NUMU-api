"""Public marketplace catalog routes.

These routes are intentionally unauthenticated — anyone can browse the
published catalog. Only `status=published` themes are returned.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies.repositories import get_marketplace_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.marketplace import (
    CatalogListResponse,
    ThemeDetailResponse,
)
from src.application.services.marketplace_service import MarketplaceService
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

router = APIRouter(prefix="/marketplace/catalog", tags=["Marketplace Catalog"])


def _svc(
    repo: Annotated[MarketplaceRepository, Depends(get_marketplace_repository)],
) -> MarketplaceService:
    return MarketplaceService(marketplace_repo=repo)


@router.get("/themes", response_model=SuccessResponse[CatalogListResponse])
async def browse_themes(
    svc: Annotated[MarketplaceService, Depends(_svc)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: str | None = None,
):
    """Browse published marketplace themes."""
    data = await svc.browse_themes(page=page, per_page=per_page, category=category)
    return SuccessResponse(data=CatalogListResponse(**data))


@router.get("/themes/{slug}", response_model=SuccessResponse[ThemeDetailResponse])
async def get_theme_detail(
    slug: str,
    svc: Annotated[MarketplaceService, Depends(_svc)],
):
    """Get detailed information about a published marketplace theme."""
    try:
        data = await svc.get_theme_detail(slug)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return SuccessResponse(data=ThemeDetailResponse(**data))

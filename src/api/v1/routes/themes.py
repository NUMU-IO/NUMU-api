"""Theme marketplace endpoints.

Public endpoints for browsing the NUMU theme catalog:
  GET  /api/v1/themes                — paginated list of published themes
  GET  /api/v1/themes/{theme_slug}   — theme detail with all versions
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.api.dependencies.repositories import (
    get_store_theme_repository,
    get_theme_repository,
    get_theme_version_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.theme_v2 import (
    ThemeDetailResponse,
    ThemeListItem,
    ThemeListResponse,
    ThemeVersionSummary,
)
from src.application.services.theme_service import ThemeService
from src.infrastructure.repositories.store_theme_repository import (
    StoreThemeRepository,
)
from src.infrastructure.repositories.theme_repository import ThemeRepository
from src.infrastructure.repositories.theme_version_repository import (
    ThemeVersionRepository,
)

router = APIRouter(prefix="/themes", tags=["Themes - Marketplace"])


def _get_theme_service(
    theme_repo: Annotated[ThemeRepository, Depends(get_theme_repository)],
    version_repo: Annotated[
        ThemeVersionRepository, Depends(get_theme_version_repository)
    ],
    store_theme_repo: Annotated[
        StoreThemeRepository, Depends(get_store_theme_repository)
    ],
) -> ThemeService:
    return ThemeService(
        theme_repo=theme_repo,
        version_repo=version_repo,
        store_theme_repo=store_theme_repo,
    )


@router.get(
    "",
    response_model=SuccessResponse[ThemeListResponse],
    summary="List published themes in the marketplace",
)
async def list_themes(
    type: Annotated[
        str | None,
        Query(description="Filter by type: 'internal' or 'external'"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    svc: ThemeService = Depends(_get_theme_service),
) -> SuccessResponse[ThemeListResponse]:
    """Return a paginated list of all published themes."""
    result = await svc.list_marketplace_themes(
        type_filter=type, page=page, per_page=per_page
    )

    # Batch-load latest versions for all returned themes (avoid N+1)
    theme_ids = [t.id for t in result["themes"]]
    latest_by_theme = await svc.version_repo.get_latest_for_themes(theme_ids)

    items = [
        ThemeListItem(
            id=str(theme.id),
            name=theme.name,
            slug=theme.slug,
            description=theme.description,
            author=theme.author,
            type=theme.type.value,
            thumbnail_url=theme.thumbnail_url,
            is_public=theme.is_public,
            status=theme.status.value,
            supported_features=theme.supported_features,
            latest_version=latest_by_theme[theme.id].version
            if theme.id in latest_by_theme
            else None,
        )
        for theme in result["themes"]
    ]

    return SuccessResponse(
        data=ThemeListResponse(
            themes=items,
            total=result["total"],
            page=result["page"],
            per_page=result["per_page"],
        )
    )


@router.get(
    "/{theme_slug}",
    response_model=SuccessResponse[ThemeDetailResponse],
    summary="Get theme detail with all versions",
)
async def get_theme(
    theme_slug: str,
    svc: ThemeService = Depends(_get_theme_service),
) -> SuccessResponse[ThemeDetailResponse]:
    """Return a theme's full details including all published versions."""
    theme, versions = await svc.get_theme_detail(theme_slug)

    version_summaries = [
        ThemeVersionSummary(
            id=str(v.id),
            version=v.version,
            is_latest=v.is_latest,
            bundle_url=v.bundle_url,
            css_url=v.css_url,
            checksum=v.checksum,
            size_bytes=v.size_bytes,
            changelog=v.changelog,
            published_at=v.published_at.isoformat() if v.published_at else None,
            created_at=v.created_at.isoformat() if v.created_at else "",
        )
        for v in versions
    ]

    return SuccessResponse(
        data=ThemeDetailResponse(
            id=str(theme.id),
            name=theme.name,
            slug=theme.slug,
            description=theme.description,
            author=theme.author,
            type=theme.type.value,
            thumbnail_url=theme.thumbnail_url,
            is_public=theme.is_public,
            status=theme.status.value,
            settings_schema=theme.settings_schema,
            section_schemas=theme.section_schemas,
            supported_features=theme.supported_features,
            versions=version_summaries,
            created_at=theme.created_at.isoformat() if theme.created_at else "",
            updated_at=theme.updated_at.isoformat() if theme.updated_at else "",
        )
    )

"""Storefront theme resolution endpoint.

Internal endpoint called by the Next.js storefront to resolve the active
theme for a store at SSR time. Not visible to end-users.

  GET /api/v1/storefront/theme/{store_id}
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies.repositories import (
    get_store_theme_repository,
    get_theme_repository,
    get_theme_version_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.theme_v2 import StorefrontThemeResponse
from src.application.services.theme_service import ThemeService
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository
from src.infrastructure.repositories.theme_repository import ThemeRepository
from src.infrastructure.repositories.theme_version_repository import (
    ThemeVersionRepository,
)

router = APIRouter(tags=["Storefront - Theme Resolution"])


def _get_svc(
    theme_repo: ThemeRepository = Depends(get_theme_repository),
    version_repo: ThemeVersionRepository = Depends(get_theme_version_repository),
    store_theme_repo: StoreThemeRepository = Depends(get_store_theme_repository),
) -> ThemeService:
    return ThemeService(
        theme_repo=theme_repo,
        version_repo=version_repo,
        store_theme_repo=store_theme_repo,
    )


@router.get(
    "/theme/{store_id}",
    response_model=SuccessResponse[StorefrontThemeResponse],
    summary="Resolve the active theme for a store (internal Next.js → FastAPI)",
    description=(
        "Returns the active theme bundle URLs, customization settings, and "
        "schema for the given store. Called by the Next.js SSR server on every "
        "cache miss. Response should be cached in Redis by the caller."
    ),
)
async def resolve_storefront_theme(
    store_id: str,
    installation_id: Annotated[
        str | None,
        Query(
            description=(
                "Optional installation_id. When set together with draft=true, "
                "returns the draft_customization for that installation instead "
                "of the live customization. Used by Next.js Draft Mode preview."
            )
        ),
    ] = None,
    draft: Annotated[
        bool,
        Query(
            description="If true and installation_id is provided, return draft_customization."
        ),
    ] = False,
    svc: ThemeService = Depends(_get_svc),
) -> SuccessResponse[StorefrontThemeResponse]:
    """Return the active theme data for a store for SSR rendering.

    Normal mode: returns the LIVE active theme + published customization.
    Preview mode (draft=true + installation_id): returns the same theme but
    with draft_customization in place of customization, so the storefront
    can render unpublished changes.
    """
    try:
        uid = UUID(store_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid store_id format",
        )

    inst_uid: UUID | None = None
    if installation_id:
        try:
            inst_uid = UUID(installation_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid installation_id format",
            )

    data = await svc.resolve_storefront_theme(
        uid, draft_installation_id=inst_uid if draft else None
    )
    return SuccessResponse(data=StorefrontThemeResponse(**data))

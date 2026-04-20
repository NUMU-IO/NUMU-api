"""V3 Theme Editor API routes.

All endpoints perform Dual-Write: V3 columns + legacy columns.
Mounted at /stores/{store_id}/themes/v3/editor/
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.dependencies.repositories import (
    get_store_theme_repository,
    get_theme_customization_version_repository,
)
from src.api.responses import SuccessResponse
from src.application.services.theme_v3_service import ThemeV3Service
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository
from src.infrastructure.repositories.theme_customization_version_repository import (
    ThemeCustomizationVersionRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/{store_id}/themes/v3/editor",
    tags=["Theme Editor V3"],
    dependencies=[Depends(verify_store_ownership)],
)


def _get_v3_service(
    store_theme_repo: Annotated[StoreThemeRepository, Depends(get_store_theme_repository)],
    version_repo: Annotated[
        ThemeCustomizationVersionRepository,
        Depends(get_theme_customization_version_repository),
    ],
) -> ThemeV3Service:
    return ThemeV3Service(store_theme_repo, version_repo)


# ── Draft (auto-save) ──────────────────────────────────────────────────────────


@router.get("/draft")
async def get_draft(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
):
    """Get the current V3 draft for the active theme.

    If no V3 data exists yet, the backend normalizes V1/V2 → V3 on the fly.
    """
    try:
        data = await svc.get_draft(store_id)
        return SuccessResponse(data=data, message="Draft retrieved")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.put("/autosave")
async def autosave_draft(
    store_id: UUID,
    payload: dict[str, Any],
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Auto-save V3 draft with Dual-Write to legacy columns.

    Called by the customizer every 2 seconds (debounced on the client).
    """
    try:
        data = await svc.autosave_draft(
            store_id=store_id,
            payload=payload,
            user_id=user_id,
            change_summary="Auto-save",
        )
        return SuccessResponse(data=data, message="Draft saved")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ── Publish ─────────────────────────────────────────────────────────────────────


@router.post("/publish")
async def publish(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Publish V3 draft with Dual-Write to all columns.

    Triggers Next.js ISR cache invalidation after successful publish.
    """
    try:
        data = await svc.publish(store_id=store_id, user_id=user_id)
        return SuccessResponse(data=data, message="Published successfully")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ── Version History ─────────────────────────────────────────────────────────────


@router.get("/versions")
async def list_versions(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List version history for the store's theme customization."""
    data = await svc.get_versions(store_id=store_id, page=page, per_page=per_page)
    return SuccessResponse(data=data, message="Versions retrieved")


@router.post("/versions/{version_id}/restore")
async def restore_version(
    store_id: UUID,
    version_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Restore a previous version as the current draft."""
    try:
        data = await svc.restore_version(
            store_id=store_id, version_id=version_id, user_id=user_id
        )
        return SuccessResponse(data=data, message="Version restored")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ── Discard Draft ───────────────────────────────────────────────────────────────


@router.post("/discard")
async def discard_draft(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
):
    """Discard V3 draft and revert to published state."""
    try:
        data = await svc.discard_draft(store_id)
        return SuccessResponse(data=data, message="Draft discarded")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ── Schemas ─────────────────────────────────────────────────────────────────────


@router.get("/schemas")
async def get_schemas(
    store_id: UUID,
    store_theme_repo: Annotated[StoreThemeRepository, Depends(get_store_theme_repository)],
):
    """Get the active theme's section/block schemas.

    For built-in themes: reads from the theme's settings_schema and section_schemas.
    For BYOT themes: reads from the marketplace theme version's schema columns.
    """
    store_theme = await store_theme_repo.get_active_for_store(store_id)
    if not store_theme:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active theme for this store",
        )

    return SuccessResponse(
        data={
            "theme_id": str(store_theme.theme_id),
            "theme_slug": store_theme.theme_slug,
            "settings_schema": store_theme.settings_schema or {},
            "section_schemas": store_theme.section_schemas or {},
        },
        message="Schemas retrieved",
    )


# ── Resolve (Dual-Read) ────────────────────────────────────────────────────────


@router.get("/resolve")
async def resolve_theme(
    store_id: UUID,
    svc: Annotated[ThemeV3Service, Depends(_get_v3_service)],
):
    """Resolve the current theme settings using Dual-Read normalization.

    Returns V3 data if available, otherwise normalizes V1/V2 → V3 in memory.
    This is the same as get_draft but semantically used by the customizer
    to initialize the editor state.
    """
    try:
        data = await svc.get_draft(store_id)
        return SuccessResponse(data=data, message="Theme resolved")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

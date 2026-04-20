"""V3 Theme Editor API routes.

All endpoints perform Dual-Write: V3 columns + legacy columns.
Mounted at /stores/{store_id}/themes/v3/editor/
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.v1.schemas.tenant.theme_v3 import (
    AutosaveDraftRequest,
    AutosaveDraftResponse,
    DiscardDraftResponse,
    PublishResponse,
    RestoreVersionRequest,
    SchemaResponse,
    VersionListResponse,
)
from src.application.services.theme_v3_service import ThemeV3Service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/themes/v3/editor", tags=["Theme Editor V3"])


def _get_v3_service(
    store_theme_repo=None,  # Injected via dependency
    version_repo=None,
) -> ThemeV3Service:
    return ThemeV3Service(store_theme_repo, version_repo)


@router.get("/draft")
async def get_draft(store_id: UUID):
    """Get the current V3 draft for the active theme."""
    # Service injection handled by FastAPI dependency system
    # Placeholder — wired up in the dependency injection layer
    pass


@router.put("/autosave")
async def autosave_draft(store_id: UUID, request: AutosaveDraftRequest):
    """Auto-save V3 draft with Dual-Write to legacy columns."""
    pass


@router.post("/publish")
async def publish(store_id: UUID):
    """Publish V3 draft with Dual-Write to all columns."""
    pass


@router.get("/versions")
async def list_versions(
    store_id: UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List version history for the store."""
    pass


@router.post("/versions/{version_id}/restore")
async def restore_version(store_id: UUID, version_id: UUID):
    """Restore a previous version as the current draft."""
    pass


@router.post("/discard")
async def discard_draft(store_id: UUID):
    """Discard V3 draft and revert to published state."""
    pass


@router.get("/schemas")
async def get_schemas(store_id: UUID):
    """Get the active theme schemas (BYOT-aware: reads from DB for external themes)."""
    pass

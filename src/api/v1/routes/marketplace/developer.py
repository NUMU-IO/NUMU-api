"""Developer marketplace routes for theme submission."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

router = APIRouter(prefix="/marketplace/developer", tags=["Marketplace Developer"])


@router.post("/themes")
async def create_listing():
    """Create a new marketplace theme listing."""
    pass


@router.get("/themes")
async def list_my_themes():
    """List themes owned by the authenticated developer."""
    pass


@router.patch("/themes/{theme_id}")
async def update_listing(theme_id: UUID):
    """Update a marketplace theme listing."""
    pass


@router.post("/themes/{theme_id}/versions")
async def submit_version(theme_id: UUID):
    """Submit a new version for build and review."""
    pass


@router.get("/themes/{theme_id}/versions")
async def list_versions(theme_id: UUID):
    """List all versions of a theme."""
    pass


@router.get("/versions/{version_id}/status")
async def check_build_status(version_id: UUID):
    """Check the build status of a submitted version."""
    pass

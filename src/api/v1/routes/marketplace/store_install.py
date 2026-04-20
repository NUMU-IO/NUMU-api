"""Store installation routes for marketplace themes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

router = APIRouter(tags=["Marketplace Store Install"])


@router.get("/stores/{store_id}/marketplace/installed")
async def list_installed(store_id: UUID):
    """List marketplace themes installed on a store."""
    pass


@router.post("/stores/{store_id}/marketplace/install")
async def install_theme(store_id: UUID):
    """Install a marketplace theme on a store."""
    pass


@router.post("/stores/{store_id}/marketplace/activate")
async def activate_theme(store_id: UUID):
    """Activate an installed marketplace theme."""
    pass


@router.delete("/stores/{store_id}/marketplace/uninstall/{theme_id}")
async def uninstall_theme(store_id: UUID, theme_id: UUID):
    """Uninstall a marketplace theme from a store."""
    pass

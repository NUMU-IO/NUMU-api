"""Public marketplace catalog routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/marketplace/catalog", tags=["Marketplace Catalog"])


@router.get("/themes")
async def browse_themes(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: str | None = None,
):
    """Browse published marketplace themes."""
    pass


@router.get("/themes/{slug}")
async def get_theme_detail(slug: str):
    """Get detailed information about a marketplace theme."""
    pass

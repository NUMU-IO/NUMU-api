"""Marketplace service layer for theme marketplace operations."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class MarketplaceService:
    """Business logic for marketplace theme operations."""

    def __init__(self, marketplace_repo, store_theme_repo=None):
        self._marketplace_repo = marketplace_repo
        self._store_theme_repo = store_theme_repo

    async def create_listing(self, developer_id: UUID, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new marketplace theme listing."""
        theme = await self._marketplace_repo.create_theme({
            "developer_id": developer_id,
            **data,
        })
        return {"id": str(theme.id), "slug": theme.slug, "status": theme.status}

    async def submit_version(
        self, theme_id: UUID, developer_id: UUID, version_string: str, **kwargs
    ) -> dict[str, Any]:
        """Submit a new version for build."""
        theme = await self._marketplace_repo.get_theme_by_id(theme_id)
        if not theme or theme.developer_id != developer_id:
            raise ValueError("Theme not found or not owned by developer")

        version = await self._marketplace_repo.create_version({
            "theme_id": theme_id,
            "version_string": version_string,
            "status": "pending_build",
            **kwargs,
        })

        # Trigger Celery build task
        try:
            from src.infrastructure.messaging.tasks.theme_marketplace_tasks import (
                build_marketplace_theme,
            )
            build_marketplace_theme.delay(str(version.id))
        except Exception as e:
            logger.warning(f"Failed to enqueue build task: {e}")

        return {"version_id": str(version.id), "status": "pending_build"}

    async def review_version(
        self, version_id: UUID, decision: str, notes: str | None = None, reviewer_id: UUID | None = None
    ) -> dict[str, Any]:
        """Admin reviews a version (approve/reject)."""
        version = await self._marketplace_repo.get_version_by_id(version_id)
        if not version:
            raise ValueError(f"Version {version_id} not found")

        if decision == "approve":
            version.status = "published"
            theme = await self._marketplace_repo.get_theme_by_id(version.theme_id)
            if theme:
                theme.status = "published"
                await self._marketplace_repo.update_theme(theme)
        elif decision == "reject":
            version.status = "rejected"
        else:
            raise ValueError(f"Invalid decision: {decision}")

        version.review_notes = notes
        version.reviewed_by = reviewer_id
        await self._marketplace_repo.update_version(version)

        return {"version_id": str(version.id), "status": version.status}

    async def browse_themes(self, page: int = 1, per_page: int = 20, category: str | None = None):
        """Browse published marketplace themes."""
        themes, total = await self._marketplace_repo.list_published(page, per_page, category)
        return {
            "themes": [
                {
                    "id": str(t.id),
                    "name": t.name,
                    "slug": t.slug,
                    "description": t.short_description or t.description,
                    "price_cents": t.price_cents,
                    "thumbnail_url": t.thumbnail_url,
                    "install_count": t.install_count,
                    "average_rating": t.average_rating,
                    "category": t.category,
                }
                for t in themes
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
        }

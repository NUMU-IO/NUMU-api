"""Repository for marketplace theme operations."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeModel,
    MarketplaceThemeVersionModel,
)


class MarketplaceRepository:
    """CRUD operations for marketplace themes and versions."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ---- Theme CRUD ----

    async def create_theme(self, data: dict[str, Any]) -> MarketplaceThemeModel:
        model = MarketplaceThemeModel(**data)
        self._session.add(model)
        await self._session.flush()
        return model

    async def get_theme_by_id(self, theme_id: UUID) -> MarketplaceThemeModel | None:
        result = await self._session.execute(
            select(MarketplaceThemeModel).where(MarketplaceThemeModel.id == theme_id)
        )
        return result.scalar_one_or_none()

    async def get_theme_by_slug(self, slug: str) -> MarketplaceThemeModel | None:
        result = await self._session.execute(
            select(MarketplaceThemeModel).where(MarketplaceThemeModel.slug == slug)
        )
        return result.scalar_one_or_none()

    async def list_published(
        self, page: int = 1, per_page: int = 20, category: str | None = None
    ) -> tuple[list[MarketplaceThemeModel], int]:
        query = select(MarketplaceThemeModel).where(
            MarketplaceThemeModel.status == "published"
        )
        if category:
            query = query.where(MarketplaceThemeModel.category == category)

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self._session.execute(count_q)).scalar() or 0

        query = query.order_by(desc(MarketplaceThemeModel.install_count))
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    async def list_by_developer(self, developer_id: UUID) -> list[MarketplaceThemeModel]:
        result = await self._session.execute(
            select(MarketplaceThemeModel)
            .where(MarketplaceThemeModel.developer_id == developer_id)
            .order_by(desc(MarketplaceThemeModel.created_at))
        )
        return list(result.scalars().all())

    async def list_pending_review(self) -> list[MarketplaceThemeVersionModel]:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(MarketplaceThemeVersionModel.status == "pending_review")
            .order_by(MarketplaceThemeVersionModel.created_at)
        )
        return list(result.scalars().all())

    async def update_theme(self, model: MarketplaceThemeModel) -> MarketplaceThemeModel:
        await self._session.flush()
        return model

    # ---- Version CRUD ----

    async def create_version(self, data: dict[str, Any]) -> MarketplaceThemeVersionModel:
        model = MarketplaceThemeVersionModel(**data)
        self._session.add(model)
        await self._session.flush()
        return model

    async def get_version_by_id(self, version_id: UUID) -> MarketplaceThemeVersionModel | None:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel).where(
                MarketplaceThemeVersionModel.id == version_id
            )
        )
        return result.scalar_one_or_none()

    async def list_versions(self, theme_id: UUID) -> list[MarketplaceThemeVersionModel]:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(MarketplaceThemeVersionModel.theme_id == theme_id)
            .order_by(desc(MarketplaceThemeVersionModel.created_at))
        )
        return list(result.scalars().all())

    async def update_version(self, model: MarketplaceThemeVersionModel) -> MarketplaceThemeVersionModel:
        await self._session.flush()
        return model

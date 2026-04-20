"""Repository for theme customization version history."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.theme_customization_version import ThemeCustomizationVersion
from src.infrastructure.database.models.tenant.theme_customization_version import (
    ThemeCustomizationVersionModel,
)


class ThemeCustomizationVersionRepository:
    """CRUD operations for theme customization version history."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, entity: ThemeCustomizationVersion) -> ThemeCustomizationVersion:
        model = ThemeCustomizationVersionModel(
            store_id=entity.store_id,
            theme_id=entity.theme_id,
            settings_blob=entity.settings_blob,
            change_summary=entity.change_summary,
            created_by=entity.created_by,
            is_published=entity.is_published,
            is_autosave=entity.is_autosave,
            version_label=entity.version_label,
        )
        self._session.add(model)
        await self._session.flush()
        entity.id = model.id
        return entity

    async def get_by_id(self, version_id: UUID) -> ThemeCustomizationVersion | None:
        result = await self._session.execute(
            select(ThemeCustomizationVersionModel).where(
                ThemeCustomizationVersionModel.id == version_id
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_store(
        self,
        store_id: UUID,
        page: int = 1,
        per_page: int = 20,
    ) -> list[ThemeCustomizationVersion]:
        offset = (page - 1) * per_page
        result = await self._session.execute(
            select(ThemeCustomizationVersionModel)
            .where(ThemeCustomizationVersionModel.store_id == store_id)
            .order_by(desc(ThemeCustomizationVersionModel.created_at))
            .offset(offset)
            .limit(per_page)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    def _to_entity(self, model: ThemeCustomizationVersionModel) -> ThemeCustomizationVersion:
        return ThemeCustomizationVersion(
            id=model.id,
            store_id=model.store_id,
            theme_id=model.theme_id,
            settings_blob=model.settings_blob,
            change_summary=model.change_summary,
            created_by=model.created_by,
            is_published=model.is_published,
            is_autosave=model.is_autosave,
            version_label=model.version_label,
        )

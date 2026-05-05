"""Repository for theme customization version history."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.theme_customization_version import ThemeCustomizationVersion
from src.infrastructure.database.models.tenant.theme_customization_version import (
    ThemeCustomizationVersionModel,
)


class ThemeCustomizationVersionRepository:
    """CRUD operations for theme customization version history."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self, entity: ThemeCustomizationVersion
    ) -> ThemeCustomizationVersion:
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
        if model.created_at:
            entity.created_at = model.created_at
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

    async def prune_autosaves(self, store_id: UUID, keep: int = 20) -> int:
        """Delete autosave rows beyond the most recent `keep` for a store.

        Published versions and labelled versions are NEVER deleted by this
        method — only `is_autosave=True` rows. Returns the number of rows
        deleted. Safe to call on every autosave; runs in a single round-trip
        via a subquery.
        """
        if keep < 0:
            keep = 0

        # Find the IDs of autosaves to keep
        keep_ids_subq = (
            select(ThemeCustomizationVersionModel.id)
            .where(
                ThemeCustomizationVersionModel.store_id == store_id,
                ThemeCustomizationVersionModel.is_autosave.is_(True),
            )
            .order_by(desc(ThemeCustomizationVersionModel.created_at))
            .limit(keep)
            .subquery()
        )

        result = await self._session.execute(
            delete(ThemeCustomizationVersionModel).where(
                ThemeCustomizationVersionModel.store_id == store_id,
                ThemeCustomizationVersionModel.is_autosave.is_(True),
                ThemeCustomizationVersionModel.id.notin_(select(keep_ids_subq)),
            )
        )
        await self._session.flush()
        return result.rowcount or 0

    def _to_entity(
        self, model: ThemeCustomizationVersionModel
    ) -> ThemeCustomizationVersion:
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
            created_at=model.created_at,
        )

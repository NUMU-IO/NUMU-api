"""ThemeVersion repository implementation."""

from __future__ import annotations

import copy
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.theme import ThemeVersion
from src.core.interfaces.repositories.theme_repository import IThemeVersionRepository
from src.infrastructure.database.models.tenant.theme import ThemeVersionModel


class ThemeVersionRepository(IThemeVersionRepository):
    """Accesses versioned theme bundles."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Mapping helpers ────────────────────────────────────────────────────────

    def _to_entity(self, model: ThemeVersionModel) -> ThemeVersion:
        published_at = model.published_at
        if isinstance(published_at, str):
            published_at = (
                datetime.fromisoformat(published_at) if published_at else None
            )
        return ThemeVersion(
            id=UUID(str(model.id)),
            created_at=model.created_at,
            updated_at=model.updated_at,
            theme_id=UUID(str(model.theme_id)),
            version=model.version,
            bundle_url=model.bundle_url,
            css_url=model.css_url,
            manifest=copy.deepcopy(model.manifest or {}),
            changelog=model.changelog,
            is_latest=model.is_latest,
            size_bytes=model.size_bytes,
            checksum=model.checksum,
            published_at=published_at,
        )

    def _to_model(self, entity: ThemeVersion) -> ThemeVersionModel:
        return ThemeVersionModel(
            id=str(entity.id),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            theme_id=str(entity.theme_id),
            version=entity.version,
            bundle_url=entity.bundle_url,
            css_url=entity.css_url,
            manifest=entity.manifest,
            changelog=entity.changelog,
            is_latest=entity.is_latest,
            size_bytes=entity.size_bytes,
            checksum=entity.checksum,
            published_at=entity.published_at,
        )

    # ── BaseRepository ─────────────────────────────────────────────────────────

    async def get_by_id(self, entity_id: UUID) -> ThemeVersion | None:
        result = await self.session.execute(
            select(ThemeVersionModel).where(ThemeVersionModel.id == str(entity_id))
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[ThemeVersion]:
        result = await self.session.execute(
            select(ThemeVersionModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: ThemeVersion) -> ThemeVersion:
        # When creating a new latest version, clear is_latest on all previous
        if entity.is_latest:
            await self.session.execute(
                update(ThemeVersionModel)
                .where(ThemeVersionModel.theme_id == str(entity.theme_id))
                .values(is_latest=False)
            )
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: ThemeVersion) -> ThemeVersion:
        result = await self.session.execute(
            select(ThemeVersionModel).where(ThemeVersionModel.id == str(entity.id))
        )
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"ThemeVersion {entity.id} not found")
        model.changelog = entity.changelog
        model.is_latest = entity.is_latest
        model.published_at = entity.published_at
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(ThemeVersionModel).where(ThemeVersionModel.id == str(entity_id))
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(ThemeVersionModel.id)))
        return result.scalar() or 0

    # ── IThemeVersionRepository ────────────────────────────────────────────────

    async def get_latest_for_theme(self, theme_id: UUID) -> ThemeVersion | None:
        result = await self.session.execute(
            select(ThemeVersionModel).where(
                ThemeVersionModel.theme_id == str(theme_id),
                ThemeVersionModel.is_latest == True,  # noqa: E712
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_theme_and_version(
        self, theme_id: UUID, version: str
    ) -> ThemeVersion | None:
        result = await self.session.execute(
            select(ThemeVersionModel).where(
                ThemeVersionModel.theme_id == str(theme_id),
                ThemeVersionModel.version == version,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_for_theme(self, theme_id: UUID) -> list[ThemeVersion]:
        result = await self.session.execute(
            select(ThemeVersionModel)
            .where(ThemeVersionModel.theme_id == str(theme_id))
            .order_by(ThemeVersionModel.created_at.desc())
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_latest_for_themes(
        self, theme_ids: list[UUID]
    ) -> dict[UUID, ThemeVersion]:
        """Batch-load the latest version for each theme.

        Returns a dict mapping theme_id -> latest ThemeVersion. Used by the
        marketplace list endpoint to avoid an N+1 query pattern.
        """
        if not theme_ids:
            return {}
        result = await self.session.execute(
            select(ThemeVersionModel).where(
                ThemeVersionModel.theme_id.in_([str(tid) for tid in theme_ids]),
                ThemeVersionModel.is_latest == True,  # noqa: E712
            )
        )
        out: dict[UUID, ThemeVersion] = {}
        for model in result.scalars().all():
            entity = self._to_entity(model)
            out[entity.theme_id] = entity
        return out

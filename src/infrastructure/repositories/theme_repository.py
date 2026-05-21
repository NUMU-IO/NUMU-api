"""Theme repository implementation."""

from __future__ import annotations

import copy
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.theme import Theme, ThemeStatus, ThemeType
from src.core.interfaces.repositories.theme_repository import IThemeRepository
from src.infrastructure.database.models.tenant.theme import ThemeModel


class ThemeRepository(IThemeRepository):
    """Theme repository — accesses the global themes catalog."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Mapping helpers ────────────────────────────────────────────────────────

    def _to_entity(self, model: ThemeModel) -> Theme:
        return Theme(
            id=UUID(str(model.id)),
            created_at=model.created_at,
            updated_at=model.updated_at,
            name=model.name,
            slug=model.slug,
            description=model.description,
            author=model.author,
            type=ThemeType(model.type),
            thumbnail_url=model.thumbnail_url,
            is_public=model.is_public,
            status=ThemeStatus(model.status),
            settings_schema=copy.deepcopy(model.settings_schema or {}),
            section_schemas=copy.deepcopy(model.section_schemas)
            if model.section_schemas
            else None,
            supported_features=copy.deepcopy(model.supported_features)
            if model.supported_features
            else None,
            created_by=UUID(str(model.created_by)) if model.created_by else None,
        )

    def _to_model(self, entity: Theme) -> ThemeModel:
        return ThemeModel(
            id=str(entity.id),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            name=entity.name,
            slug=entity.slug,
            description=entity.description,
            author=entity.author,
            type=entity.type.value,
            thumbnail_url=entity.thumbnail_url,
            is_public=entity.is_public,
            status=entity.status.value,
            settings_schema=entity.settings_schema,
            section_schemas=entity.section_schemas,
            supported_features=entity.supported_features,
            created_by=str(entity.created_by) if entity.created_by else None,
        )

    # ── BaseRepository ─────────────────────────────────────────────────────────

    async def get_by_id(self, entity_id: UUID) -> Theme | None:
        result = await self.session.execute(
            select(ThemeModel).where(ThemeModel.id == str(entity_id))
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Theme]:
        result = await self.session.execute(
            select(ThemeModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: Theme) -> Theme:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Theme) -> Theme:
        result = await self.session.execute(
            select(ThemeModel).where(ThemeModel.id == str(entity.id))
        )
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"Theme {entity.id} not found")
        model.name = entity.name
        model.slug = entity.slug
        model.description = entity.description
        model.author = entity.author
        model.type = entity.type.value
        model.thumbnail_url = entity.thumbnail_url
        model.is_public = entity.is_public
        model.status = entity.status.value
        model.settings_schema = entity.settings_schema
        model.section_schemas = entity.section_schemas
        model.supported_features = entity.supported_features
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(ThemeModel).where(ThemeModel.id == str(entity_id))
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(ThemeModel.id)))
        return result.scalar() or 0

    # ── IThemeRepository ───────────────────────────────────────────────────────

    async def get_by_slug(self, slug: str) -> Theme | None:
        result = await self.session.execute(
            select(ThemeModel).where(ThemeModel.slug == slug)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_published(
        self,
        type_filter: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Theme]:
        query = select(ThemeModel).where(ThemeModel.status == "published")
        if type_filter:
            query = query.where(ThemeModel.type == type_filter)
        query = query.order_by(ThemeModel.name).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_published(self, type_filter: str | None = None) -> int:
        query = select(func.count(ThemeModel.id)).where(
            ThemeModel.status == "published"
        )
        if type_filter:
            query = query.where(ThemeModel.type == type_filter)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def slug_exists(self, slug: str) -> bool:
        result = await self.session.execute(
            select(ThemeModel.id).where(ThemeModel.slug == slug)
        )
        return result.scalar_one_or_none() is not None

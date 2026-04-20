"""StoreTheme repository implementation."""

from __future__ import annotations

import copy
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.entities.theme import StoreTheme, ThemeType
from src.core.interfaces.repositories.theme_repository import IStoreThemeRepository
from src.infrastructure.database.models.tenant.theme import StoreThemeModel


class StoreThemeRepository(IStoreThemeRepository):
    """Manages per-store theme installations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Mapping helpers ────────────────────────────────────────────────────────

    def _to_entity(self, model: StoreThemeModel) -> StoreTheme:
        installed_at = model.installed_at
        if isinstance(installed_at, str):
            installed_at = (
                datetime.fromisoformat(installed_at) if installed_at else None
            )
        activated_at = model.activated_at
        if isinstance(activated_at, str):
            activated_at = (
                datetime.fromisoformat(activated_at) if activated_at else None
            )

        # Denormalized theme/version data from eager-loaded relationships
        theme_slug = None
        theme_name = None
        theme_type = None
        theme_thumbnail_url = None
        settings_schema = None
        section_schemas = None
        theme_version_str = None
        bundle_url = None
        css_url = None

        if model.theme is not None:
            theme_slug = model.theme.slug
            theme_name = model.theme.name
            theme_type = ThemeType(model.theme.type)
            theme_thumbnail_url = model.theme.thumbnail_url
            settings_schema = copy.deepcopy(model.theme.settings_schema or {})
            section_schemas = (
                copy.deepcopy(model.theme.section_schemas)
                if model.theme.section_schemas
                else None
            )

        if model.theme_version is not None:
            theme_version_str = model.theme_version.version
            bundle_url = model.theme_version.bundle_url
            css_url = model.theme_version.css_url

        return StoreTheme(
            id=UUID(str(model.id)),
            created_at=model.created_at,
            updated_at=model.updated_at,
            tenant_id=UUID(str(model.tenant_id)),
            store_id=UUID(str(model.store_id)),
            theme_id=UUID(str(model.theme_id)),
            theme_version_id=UUID(str(model.theme_version_id)),
            is_active=model.is_active,
            customization=copy.deepcopy(model.customization or {}),
            draft_customization=copy.deepcopy(model.draft_customization or {}),
            customization_v3=copy.deepcopy(model.customization_v3 or {}),
            draft_customization_v3=copy.deepcopy(model.draft_customization_v3 or {}),
            installed_at=installed_at,
            activated_at=activated_at,
            # Denormalized
            theme_slug=theme_slug,
            theme_name=theme_name,
            theme_type=theme_type,
            theme_thumbnail_url=theme_thumbnail_url,
            settings_schema=settings_schema,
            section_schemas=section_schemas,
            theme_version=theme_version_str,
            bundle_url=bundle_url,
            css_url=css_url,
        )

    def _to_model(self, entity: StoreTheme) -> StoreThemeModel:
        return StoreThemeModel(
            id=str(entity.id),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            tenant_id=str(entity.tenant_id),
            store_id=str(entity.store_id),
            theme_id=str(entity.theme_id),
            theme_version_id=str(entity.theme_version_id),
            is_active=entity.is_active,
            customization=entity.customization,
            draft_customization=entity.draft_customization,
            customization_v3=entity.customization_v3,
            draft_customization_v3=entity.draft_customization_v3,
            installed_at=entity.installed_at,
            activated_at=entity.activated_at,
        )

    def _base_query(self):
        """Base query with eager loading of theme and version."""
        return select(StoreThemeModel).options(
            selectinload(StoreThemeModel.theme),
            selectinload(StoreThemeModel.theme_version),
        )

    # ── BaseRepository ─────────────────────────────────────────────────────────

    async def get_by_id(self, entity_id: UUID) -> StoreTheme | None:
        result = await self.session.execute(
            self._base_query().where(StoreThemeModel.id == str(entity_id))
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[StoreTheme]:
        result = await self.session.execute(
            self._base_query().offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: StoreTheme) -> StoreTheme:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        # Reload with eager relationships
        result = await self.session.execute(
            self._base_query().where(StoreThemeModel.id == model.id)
        )
        refreshed = result.scalar_one()
        return self._to_entity(refreshed)

    async def update(self, entity: StoreTheme) -> StoreTheme:
        result = await self.session.execute(
            select(StoreThemeModel).where(StoreThemeModel.id == str(entity.id))
        )
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"StoreTheme {entity.id} not found")
        model.is_active = entity.is_active
        model.customization = entity.customization
        model.draft_customization = entity.draft_customization
        model.installed_at = entity.installed_at
        model.activated_at = entity.activated_at
        model.theme_version_id = str(entity.theme_version_id)
        await self.session.flush()
        # Reload with eager relationships
        result2 = await self.session.execute(
            self._base_query().where(StoreThemeModel.id == str(entity.id))
        )
        refreshed = result2.scalar_one()
        return self._to_entity(refreshed)

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(StoreThemeModel).where(StoreThemeModel.id == str(entity_id))
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(StoreThemeModel.id)))
        return result.scalar() or 0

    # ── IStoreThemeRepository ──────────────────────────────────────────────────

    async def get_active_for_store(self, store_id: UUID) -> StoreTheme | None:
        """Return the active installation for a store (always at most one)."""
        result = await self.session.execute(
            self._base_query().where(
                StoreThemeModel.store_id == str(store_id),
                StoreThemeModel.is_active == True,  # noqa: E712
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_installations_for_store(self, store_id: UUID) -> list[StoreTheme]:
        """Return all installations for a store (ordered by installation date)."""
        result = await self.session.execute(
            self._base_query()
            .where(StoreThemeModel.store_id == str(store_id))
            .order_by(
                StoreThemeModel.is_active.desc(), StoreThemeModel.created_at.desc()
            )
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_installation(
        self, store_id: UUID, installation_id: UUID
    ) -> StoreTheme | None:
        """Get an installation by ID, verifying it belongs to the store."""
        result = await self.session.execute(
            self._base_query().where(
                StoreThemeModel.id == str(installation_id),
                StoreThemeModel.store_id == str(store_id),
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def deactivate_all_for_store(self, store_id: UUID) -> None:
        """Set is_active=False on all installations for a store."""
        await self.session.execute(
            update(StoreThemeModel)
            .where(StoreThemeModel.store_id == str(store_id))
            .values(is_active=False)
        )

    async def installation_exists(self, store_id: UUID, theme_id: UUID) -> bool:
        """Check whether a theme is already installed on this store."""
        result = await self.session.execute(
            select(StoreThemeModel.id).where(
                StoreThemeModel.store_id == str(store_id),
                StoreThemeModel.theme_id == str(theme_id),
            )
        )
        return result.scalar_one_or_none() is not None

"""Repository for marketplace theme operations."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.marketplace_theme import (
    MarketplaceTheme,
    MarketplaceThemeInstallation,
    MarketplaceThemeStatus,
    MarketplaceThemeVersion,
    MarketplaceVersionStatus,
)
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeInstallationModel,
    MarketplaceThemeModel,
    MarketplaceThemeVersionModel,
)


class MarketplaceRepository:
    """CRUD operations for marketplace themes, versions, and installations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ── Mapping ───────────────────────────────────────────────────────────────

    def _theme_to_entity(self, m: MarketplaceThemeModel) -> MarketplaceTheme:
        return MarketplaceTheme(
            id=m.id,
            created_at=m.created_at,
            updated_at=m.updated_at,
            developer_id=m.developer_id,
            name=m.name,
            slug=m.slug,
            description=m.description,
            short_description=m.short_description,
            price_cents=m.price_cents,
            currency=m.currency,
            status=MarketplaceThemeStatus(m.status),
            thumbnail_url=m.thumbnail_url,
            preview_url=m.preview_url,
            demo_store_url=m.demo_store_url,
            tags=copy.deepcopy(m.tags or []),
            category=m.category,
            supported_languages=copy.deepcopy(m.supported_languages or []),
            supported_features=copy.deepcopy(m.supported_features or {}),
            install_count=m.install_count,
            average_rating=m.average_rating,
            review_count=m.review_count,
        )

    def _version_to_entity(
        self, m: MarketplaceThemeVersionModel
    ) -> MarketplaceThemeVersion:
        return MarketplaceThemeVersion(
            id=m.id,
            created_at=m.created_at,
            updated_at=m.created_at,
            theme_id=m.theme_id,
            version_string=m.version_string,
            bundle_url=m.bundle_url,
            css_url=m.css_url,
            settings_schema=copy.deepcopy(m.settings_schema or {}),
            section_schemas=copy.deepcopy(m.section_schemas or {}),
            presets=copy.deepcopy(m.presets or {}),
            release_notes=m.release_notes,
            status=MarketplaceVersionStatus(m.status),
            build_log=m.build_log,
            size_bytes=m.size_bytes,
            checksum=m.checksum,
            source_zip_path=m.source_zip_path,
            review_notes=m.review_notes,
            reviewed_by=m.reviewed_by,
        )

    def _installation_to_entity(
        self, m: MarketplaceThemeInstallationModel
    ) -> MarketplaceThemeInstallation:
        return MarketplaceThemeInstallation(
            id=m.id,
            store_id=m.store_id,
            marketplace_theme_id=m.marketplace_theme_id,
            marketplace_version_id=m.marketplace_version_id,
            is_active=m.is_active,
            installed_at=m.installed_at,
            uninstalled_at=m.uninstalled_at,
        )

    # ── Theme CRUD ────────────────────────────────────────────────────────────

    async def create_theme(self, data: dict[str, Any]) -> MarketplaceTheme:
        model = MarketplaceThemeModel(**data)
        self._session.add(model)
        await self._session.flush()
        return self._theme_to_entity(model)

    async def get_theme_by_id(self, theme_id: UUID) -> MarketplaceTheme | None:
        result = await self._session.execute(
            select(MarketplaceThemeModel).where(MarketplaceThemeModel.id == theme_id)
        )
        m = result.scalar_one_or_none()
        return self._theme_to_entity(m) if m else None

    async def get_theme_by_slug(self, slug: str) -> MarketplaceTheme | None:
        result = await self._session.execute(
            select(MarketplaceThemeModel).where(MarketplaceThemeModel.slug == slug)
        )
        m = result.scalar_one_or_none()
        return self._theme_to_entity(m) if m else None

    async def list_published(
        self, page: int = 1, per_page: int = 20, category: str | None = None
    ) -> tuple[list[MarketplaceTheme], int]:
        base = select(MarketplaceThemeModel).where(
            MarketplaceThemeModel.status == MarketplaceThemeStatus.PUBLISHED.value
        )
        if category:
            base = base.where(MarketplaceThemeModel.category == category)

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_q)).scalar() or 0

        q = (
            base.order_by(desc(MarketplaceThemeModel.install_count))
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self._session.execute(q)
        return [self._theme_to_entity(m) for m in result.scalars().all()], total

    async def list_by_developer(self, developer_id: UUID) -> list[MarketplaceTheme]:
        result = await self._session.execute(
            select(MarketplaceThemeModel)
            .where(MarketplaceThemeModel.developer_id == developer_id)
            .order_by(desc(MarketplaceThemeModel.created_at))
        )
        return [self._theme_to_entity(m) for m in result.scalars().all()]

    async def list_pending_review(self) -> list[MarketplaceThemeVersion]:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(
                MarketplaceThemeVersionModel.status
                == MarketplaceVersionStatus.PENDING_REVIEW.value
            )
            .order_by(MarketplaceThemeVersionModel.created_at)
        )
        return [self._version_to_entity(m) for m in result.scalars().all()]

    async def update_theme(
        self, theme_id: UUID, fields: dict[str, Any]
    ) -> MarketplaceTheme | None:
        await self._session.execute(
            update(MarketplaceThemeModel)
            .where(MarketplaceThemeModel.id == theme_id)
            .values(**fields)
        )
        await self._session.flush()
        return await self.get_theme_by_id(theme_id)

    async def increment_install_count(self, theme_id: UUID, delta: int = 1) -> None:
        await self._session.execute(
            update(MarketplaceThemeModel)
            .where(MarketplaceThemeModel.id == theme_id)
            .values(install_count=MarketplaceThemeModel.install_count + delta)
        )
        await self._session.flush()

    # ── Version CRUD ──────────────────────────────────────────────────────────

    async def create_version(self, data: dict[str, Any]) -> MarketplaceThemeVersion:
        model = MarketplaceThemeVersionModel(**data)
        self._session.add(model)
        await self._session.flush()
        return self._version_to_entity(model)

    async def get_version_by_id(
        self, version_id: UUID
    ) -> MarketplaceThemeVersion | None:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel).where(
                MarketplaceThemeVersionModel.id == version_id
            )
        )
        m = result.scalar_one_or_none()
        return self._version_to_entity(m) if m else None

    async def get_latest_published_version(
        self, theme_id: UUID
    ) -> MarketplaceThemeVersion | None:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(
                MarketplaceThemeVersionModel.theme_id == theme_id,
                MarketplaceThemeVersionModel.status
                == MarketplaceVersionStatus.PUBLISHED.value,
            )
            .order_by(desc(MarketplaceThemeVersionModel.created_at))
            .limit(1)
        )
        m = result.scalar_one_or_none()
        return self._version_to_entity(m) if m else None

    async def list_versions(self, theme_id: UUID) -> list[MarketplaceThemeVersion]:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(MarketplaceThemeVersionModel.theme_id == theme_id)
            .order_by(desc(MarketplaceThemeVersionModel.created_at))
        )
        return [self._version_to_entity(m) for m in result.scalars().all()]

    async def update_version(
        self, version_id: UUID, fields: dict[str, Any]
    ) -> MarketplaceThemeVersion | None:
        await self._session.execute(
            update(MarketplaceThemeVersionModel)
            .where(MarketplaceThemeVersionModel.id == version_id)
            .values(**fields)
        )
        await self._session.flush()
        return await self.get_version_by_id(version_id)

    # ── Installation CRUD ─────────────────────────────────────────────────────

    async def get_installation(
        self, store_id: UUID, marketplace_theme_id: UUID
    ) -> MarketplaceThemeInstallation | None:
        result = await self._session.execute(
            select(MarketplaceThemeInstallationModel).where(
                MarketplaceThemeInstallationModel.store_id == store_id,
                MarketplaceThemeInstallationModel.marketplace_theme_id
                == marketplace_theme_id,
            )
        )
        m = result.scalar_one_or_none()
        return self._installation_to_entity(m) if m else None

    async def list_installations(
        self, store_id: UUID, include_uninstalled: bool = False
    ) -> list[MarketplaceThemeInstallation]:
        q = select(MarketplaceThemeInstallationModel).where(
            MarketplaceThemeInstallationModel.store_id == store_id
        )
        if not include_uninstalled:
            q = q.where(MarketplaceThemeInstallationModel.uninstalled_at.is_(None))
        q = q.order_by(desc(MarketplaceThemeInstallationModel.installed_at))
        result = await self._session.execute(q)
        return [self._installation_to_entity(m) for m in result.scalars().all()]

    async def create_or_reactivate_installation(
        self,
        store_id: UUID,
        marketplace_theme_id: UUID,
        marketplace_version_id: UUID,
    ) -> MarketplaceThemeInstallation:
        """Insert a new install row, or reactivate an existing one if the
        store had uninstalled this theme before."""
        existing = await self._session.execute(
            select(MarketplaceThemeInstallationModel).where(
                MarketplaceThemeInstallationModel.store_id == store_id,
                MarketplaceThemeInstallationModel.marketplace_theme_id
                == marketplace_theme_id,
            )
        )
        m = existing.scalar_one_or_none()
        now = datetime.now(UTC)
        if m:
            m.marketplace_version_id = marketplace_version_id
            m.uninstalled_at = None
            m.installed_at = now
        else:
            m = MarketplaceThemeInstallationModel(
                store_id=store_id,
                marketplace_theme_id=marketplace_theme_id,
                marketplace_version_id=marketplace_version_id,
                is_active=False,
                installed_at=now,
            )
            self._session.add(m)
        await self._session.flush()
        return self._installation_to_entity(m)

    async def set_active_installation(
        self, store_id: UUID, marketplace_theme_id: UUID | None
    ) -> None:
        """Make the given installation the active one (or none).

        Toggles `is_active` so only the named theme is active. Pass
        `marketplace_theme_id=None` to deactivate all marketplace
        installations for the store.
        """
        await self._session.execute(
            update(MarketplaceThemeInstallationModel)
            .where(MarketplaceThemeInstallationModel.store_id == store_id)
            .values(is_active=False)
        )
        if marketplace_theme_id is not None:
            await self._session.execute(
                update(MarketplaceThemeInstallationModel)
                .where(
                    MarketplaceThemeInstallationModel.store_id == store_id,
                    MarketplaceThemeInstallationModel.marketplace_theme_id
                    == marketplace_theme_id,
                )
                .values(is_active=True)
            )
        await self._session.flush()

    async def mark_uninstalled(
        self, store_id: UUID, marketplace_theme_id: UUID
    ) -> bool:
        result = await self._session.execute(
            update(MarketplaceThemeInstallationModel)
            .where(
                MarketplaceThemeInstallationModel.store_id == store_id,
                MarketplaceThemeInstallationModel.marketplace_theme_id
                == marketplace_theme_id,
            )
            .values(uninstalled_at=datetime.now(UTC), is_active=False)
        )
        await self._session.flush()
        return (result.rowcount or 0) > 0

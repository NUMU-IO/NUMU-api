"""V3 Theme Editor service layer.

Handles auto-save drafts, publish with Dual-Write, version history,
restore, and BYOT initialization.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.core.entities.theme_customization_version import ThemeCustomizationVersion
from src.core.entities.theme_settings_v3 import ThemeSettingsV3
from src.application.services.theme_v3_mappers import map_v3_to_legacy_store_settings

logger = logging.getLogger(__name__)


class ThemeV3Service:
    """Service for V3 theme editor operations."""

    def __init__(self, store_theme_repo, version_repo):
        self._store_theme_repo = store_theme_repo
        self._version_repo = version_repo

    async def get_draft(self, store_id: UUID) -> dict[str, Any]:
        """Get the current V3 draft. Falls back to normalizing V2 data."""
        store_theme = await self._store_theme_repo.get_active_by_store(store_id)
        if not store_theme:
            return {}

        # Prefer V3 draft
        if store_theme.draft_customization_v3 and store_theme.draft_customization_v3.get("schema_version") == 3:
            return store_theme.draft_customization_v3

        # Prefer V3 published
        if store_theme.customization_v3 and store_theme.customization_v3.get("schema_version") == 3:
            return store_theme.customization_v3

        # Fall back to normalizing legacy data
        from src.application.services.theme_v3_mappers import normalize_legacy_to_v3
        legacy = store_theme.customization or store_theme.draft_customization or {}
        if legacy:
            v3 = normalize_legacy_to_v3(legacy)
            return v3.model_dump()

        return {}

    async def autosave_draft(
        self,
        store_id: UUID,
        payload: dict[str, Any],
        user_id: UUID | None = None,
        change_summary: str | None = None,
    ) -> dict[str, Any]:
        """Auto-save a V3 draft with Dual-Write to legacy columns."""
        store_theme = await self._store_theme_repo.get_active_by_store(store_id)
        if not store_theme:
            raise ValueError(f"No active theme for store {store_id}")

        # Validate V3 payload
        v3 = ThemeSettingsV3(**payload)
        v3_dict = v3.model_dump()

        # DUAL-WRITE: Write V3 to new column
        store_theme.draft_customization_v3 = v3_dict

        # DUAL-WRITE: Map V3 -> legacy and write to old column
        legacy = map_v3_to_legacy_store_settings(v3)
        store_theme.draft_customization = legacy

        await self._store_theme_repo.update(store_theme)

        # Create version record (autosave)
        version = ThemeCustomizationVersion(
            store_id=store_id,
            theme_id=v3.theme_id,
            settings_blob=v3_dict,
            change_summary=change_summary or "Auto-save",
            created_by=user_id,
            is_published=False,
            is_autosave=True,
        )
        await self._version_repo.create(version)

        logger.info(f"V3 autosave for store {store_id}, version {version.id}")
        return v3_dict

    async def publish(
        self,
        store_id: UUID,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Publish V3 draft with Dual-Write to all columns."""
        store_theme = await self._store_theme_repo.get_active_by_store(store_id)
        if not store_theme:
            raise ValueError(f"No active theme for store {store_id}")

        draft_v3 = store_theme.draft_customization_v3
        if not draft_v3 or draft_v3.get("schema_version") != 3:
            raise ValueError("No V3 draft to publish")

        v3 = ThemeSettingsV3(**draft_v3)
        v3_dict = v3.model_dump()

        # DUAL-WRITE: Publish V3
        store_theme.customization_v3 = v3_dict
        store_theme.draft_customization_v3 = {}

        # DUAL-WRITE: Map V3 -> legacy and publish to old columns
        legacy = map_v3_to_legacy_store_settings(v3)
        store_theme.customization = legacy
        store_theme.draft_customization = {}

        await self._store_theme_repo.update(store_theme)

        # Create published version record
        version = ThemeCustomizationVersion(
            store_id=store_id,
            theme_id=v3.theme_id,
            settings_blob=v3_dict,
            change_summary="Published",
            created_by=user_id,
            is_published=True,
            is_autosave=False,
        )
        await self._version_repo.create(version)

        # Trigger Next.js cache invalidation
        await self._revalidate_storefront(store_id)

        logger.info(f"V3 published for store {store_id}")
        return v3_dict

    async def get_versions(
        self,
        store_id: UUID,
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """List version history for a store."""
        versions = await self._version_repo.list_by_store(
            store_id=store_id,
            page=page,
            per_page=per_page,
        )
        return [
            {
                "id": str(v.id),
                "theme_id": v.theme_id,
                "change_summary": v.change_summary,
                "is_published": v.is_published,
                "is_autosave": v.is_autosave,
                "version_label": v.version_label,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "created_by": str(v.created_by) if v.created_by else None,
            }
            for v in versions
        ]

    async def restore_version(
        self,
        store_id: UUID,
        version_id: UUID,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Restore a previous version as the current draft."""
        version = await self._version_repo.get_by_id(version_id)
        if not version or version.store_id != store_id:
            raise ValueError(f"Version {version_id} not found for store {store_id}")

        # Use the version's settings_blob as the new draft
        return await self.autosave_draft(
            store_id=store_id,
            payload=version.settings_blob,
            user_id=user_id,
            change_summary=f"Restored from version {version_id}",
        )

    async def discard_draft(self, store_id: UUID) -> dict[str, Any]:
        """Discard V3 draft and revert to published state."""
        store_theme = await self._store_theme_repo.get_active_by_store(store_id)
        if not store_theme:
            raise ValueError(f"No active theme for store {store_id}")

        published = store_theme.customization_v3 or {}
        store_theme.draft_customization_v3 = {}
        store_theme.draft_customization = {}
        await self._store_theme_repo.update(store_theme)
        return published

    async def _revalidate_storefront(self, store_id: UUID) -> None:
        """Trigger Next.js ISR cache invalidation after publish."""
        try:
            from src.infrastructure.external_services.nextjs_revalidation import (
                revalidate_on_customization_publish,
            )
            await revalidate_on_customization_publish(store_id)
        except Exception as e:
            logger.warning(f"Storefront revalidation failed for store {store_id}: {e}")

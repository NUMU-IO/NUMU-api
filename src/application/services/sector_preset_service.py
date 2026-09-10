"""Applies a sector preset to a store.

Everything here is additive and idempotent: a field, category or capability
that already exists is left exactly as the merchant has it and reported as
skipped. Applying the same preset twice changes nothing the second time,
which is what makes the button safe to press on a live store.

The theme layout is written to ``draft_customization_v3``, never to the live
``customization_v3``. A preset must not be able to rearrange a storefront
that is currently taking orders — the merchant reviews the draft in the
customizer and publishes it themselves.
"""

from __future__ import annotations

from typing import Any

from src.application.services.capability_service import CAPABILITIES
from src.core.entities.category import Category
from src.core.entities.metafield import MetafieldDefinition
from src.core.entities.store import Store
from src.core.logging import get_logger
from src.core.sector_presets import SectorPreset
from src.infrastructure.repositories.category_repository import CategoryRepository
from src.infrastructure.repositories.metafield_repository import (
    MetafieldDefinitionRepository,
)
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.repositories.store_theme_repository import (
    StoreThemeRepository,
)

logger = get_logger(__name__)


class SectorPresetService:
    """Seeds a store with a sector's fields, categories, capabilities and layout."""

    def __init__(
        self,
        *,
        definition_repo: MetafieldDefinitionRepository,
        category_repo: CategoryRepository,
        store_repo: StoreRepository,
        store_theme_repo: StoreThemeRepository,
    ) -> None:
        self.definition_repo = definition_repo
        self.category_repo = category_repo
        self.store_repo = store_repo
        self.store_theme_repo = store_theme_repo

    async def apply(
        self,
        store: Store,
        preset: SectorPreset,
        *,
        apply_categories: bool = True,
        apply_theme: bool = False,
    ) -> dict[str, Any]:
        """Apply ``preset`` to ``store`` and return a report of what changed."""
        report: dict[str, Any] = {
            "preset": preset.key,
            "fields_created": 0,
            "fields_skipped": 0,
            "categories_created": 0,
            "categories_skipped": 0,
            "capabilities_enabled": [],
            "capabilities_unavailable": [],
            "theme": "not_requested",
        }

        for preset_field in preset.fields:
            existing = await self.definition_repo.get_by_key(
                store.id,
                preset_field.owner_type,
                preset.namespace,
                preset_field.key,
            )
            if existing:
                report["fields_skipped"] += 1
                continue
            await self.definition_repo.create(
                MetafieldDefinition(
                    store_id=store.id,
                    tenant_id=store.tenant_id,
                    owner_type=preset_field.owner_type,
                    namespace=preset.namespace,
                    key=preset_field.key,
                    type=preset_field.type,
                    name=preset_field.name,
                    description=preset_field.name_ar,
                    is_public=preset_field.is_public,
                )
            )
            report["fields_created"] += 1

        if apply_categories:
            for position, preset_category in enumerate(preset.categories):
                existing_category = await self.category_repo.get_by_slug(
                    store.id, preset_category.slug
                )
                if existing_category:
                    report["categories_skipped"] += 1
                    continue
                await self.category_repo.create(
                    Category(
                        store_id=store.id,
                        tenant_id=store.tenant_id,
                        name=preset_category.name,
                        slug=preset_category.slug,
                        description=preset_category.name_ar,
                        position=position,
                    )
                )
                report["categories_created"] += 1

        settings = dict(store.settings or {})
        capabilities = dict(settings.get("capabilities") or {})
        for key in preset.capabilities:
            capability = CAPABILITIES.get(key)
            if capability is None or not capability.implemented:
                report["capabilities_unavailable"].append(key)
                continue
            capabilities[key] = True
            report["capabilities_enabled"].append(key)
        settings["capabilities"] = capabilities
        settings["sector"] = preset.key
        store.settings = settings
        await self.store_repo.update(store)

        if apply_theme:
            report["theme"] = await self._apply_theme(store, preset)

        logger.info(
            "sector_preset_applied",
            store_id=str(store.id),
            preset=preset.key,
            fields_created=report["fields_created"],
            categories_created=report["categories_created"],
            theme=report["theme"],
        )
        return report

    async def _apply_theme(self, store: Store, preset: SectorPreset) -> str:
        """Write the preset's home layout into the active theme's V3 draft."""
        store_theme = await self.store_theme_repo.get_active_for_store(store.id)
        if not store_theme:
            return "skipped_no_active_theme"

        available = _available_section_types(store_theme.section_schemas)
        if not available:
            return "skipped_no_section_schema"

        sections = [s for s in preset.home_sections if s in available]
        if not sections:
            return "skipped_no_matching_sections"

        draft = dict(
            store_theme.draft_customization_v3 or store_theme.customization_v3 or {}
        )
        draft.setdefault("schema_version", 3)
        templates = dict(draft.get("templates") or {})
        home = dict(templates.get("home") or {})
        existing_sections = dict(home.get("sections") or {})

        for index, section_type in enumerate(sections):
            instance_id = f"{section_type}-{index + 1}"
            existing_sections.setdefault(
                instance_id,
                {"type": section_type, "disabled": False, "settings": {}},
            )

        home["sections"] = existing_sections
        home["order"] = [
            f"{section_type}-{index + 1}" for index, section_type in enumerate(sections)
        ]
        templates["home"] = home
        draft["templates"] = templates

        store_theme.draft_customization_v3 = draft
        await self.store_theme_repo.update(store_theme)
        return "draft_updated"


def _available_section_types(section_schemas: Any) -> set[str]:
    """Return the section types the active theme actually declares.

    Themes disagree on their section vocabulary — ``categories`` exists in
    one V3 theme and ``collection_list`` in another — so a preset's layout is
    filtered against the live theme rather than assumed. Both the dict-keyed
    and list-of-objects manifest shapes are in the wild.
    """
    if isinstance(section_schemas, dict):
        return set(section_schemas.keys())
    if isinstance(section_schemas, list):
        return {
            entry["type"]
            for entry in section_schemas
            if isinstance(entry, dict) and isinstance(entry.get("type"), str)
        }
    return set()

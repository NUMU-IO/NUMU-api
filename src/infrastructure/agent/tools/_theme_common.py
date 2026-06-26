"""Shared helpers for the Agent's theme tools.

Constructs the existing ThemeV3Service (Constitution IV — reuse the theme-editor-v3
write path) and provides section-schema lookups that tolerate both the dict and
Shopify-style list shapes of `section_schemas`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.theme_v3_service import ThemeV3Service
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository
from src.infrastructure.repositories.theme_customization_version_repository import (
    ThemeCustomizationVersionRepository,
)


def build_v3_service(session: AsyncSession) -> ThemeV3Service:
    return ThemeV3Service(
        store_theme_repo=StoreThemeRepository(session),
        version_repo=ThemeCustomizationVersionRepository(session),
    )


def known_section_types(section_schemas: Any) -> list[str]:
    """Available section types for the active theme (dict keys or list `type`s)."""
    if not section_schemas:
        return []
    if isinstance(section_schemas, dict):
        return sorted(section_schemas.keys())
    if isinstance(section_schemas, list):
        return sorted({
            s.get("type")
            for s in section_schemas
            if isinstance(s, dict) and s.get("type")
        })
    return []


def section_settings_schema(
    section_schemas: Any, section_type: str
) -> list[dict] | None:
    """Return the `settings` list (JSON-schema-ish) for one section type, if any."""
    entry: Any = None
    if isinstance(section_schemas, dict):
        entry = section_schemas.get(section_type)
    elif isinstance(section_schemas, list):
        entry = next(
            (
                s
                for s in section_schemas
                if isinstance(s, dict) and s.get("type") == section_type
            ),
            None,
        )
    if not isinstance(entry, dict):
        return None
    settings = entry.get("settings")
    return settings if isinstance(settings, list) else []


def build_section_settings(
    schema_settings: list[dict], provided: dict
) -> tuple[dict | None, str | None]:
    """Apply schema defaults, then overlay provided values.

    Returns (settings, error). Rejects unknown setting keys (FR-011) so an
    invalid proposal is never surfaced.
    """
    known_ids = {
        s.get("id") for s in schema_settings if isinstance(s, dict) and s.get("id")
    }
    for key in provided:
        if known_ids and key not in known_ids:
            return None, f"Unknown setting '{key}' for this section type."
    result: dict = {
        s["id"]: s["default"]
        for s in schema_settings
        if isinstance(s, dict) and "id" in s and "default" in s
    }
    result.update({
        k: v for k, v in provided.items() if not known_ids or k in known_ids
    })
    return result, None


def next_section_id(sections: dict, section_type: str) -> str:
    """Compute the next `<type>-<idx>` id not already used in the page."""
    max_idx = -1
    prefix = f"{section_type}-"
    for sid in sections or {}:
        if sid.startswith(prefix):
            suffix = sid[len(prefix) :]
            if suffix.isdigit():
                max_idx = max(max_idx, int(suffix))
    return f"{section_type}-{max_idx + 1}"

"""Dual-Write mappers for V3 theme settings.

map_v3_to_legacy_store_settings: V3 -> V2/V1 flat format (backward compat)
normalize_legacy_to_v3: V1/V2 -> V3 (forward normalization for Dual-Read)
resolve_theme_settings: Picks the best available data and returns V3.
"""

from __future__ import annotations

from typing import Any

from src.core.entities.theme_settings_v3 import (
    BlockInstance,
    ExternalThemeMetadata,
    PageTemplate,
    SectionGroup,
    SectionInstance,
    ThemeSettingsV3,
)


def map_v3_to_legacy_store_settings(v3: ThemeSettingsV3) -> dict[str, Any]:
    """Convert a V3 payload back into the legacy flat format.

    This ensures the old Vite SPA storefront never sees a data shape
    it doesn't understand. Called on every V3 save and publish.
    """
    legacy: dict[str, Any] = {
        "schema_version": 2,
        "theme": {
            "base_theme": v3.theme_id,
            **v3.global_settings,
        },
        "identity": v3.global_settings.get("identity", {}),
    }

    # Extract hero from home template
    home_tpl = v3.templates.get("home")
    if home_tpl:
        for section in home_tpl.sections.values():
            if section.type == "hero":
                legacy["hero"] = {
                    "headline": section.settings.get("headline", ""),
                    "headline_ar": section.settings.get("headline_ar", ""),
                    "subtitle": section.settings.get("subtitle", ""),
                    "hero_image_url": section.settings.get("background_image", ""),
                    "cta_text": section.settings.get("cta_text", ""),
                    "cta_link": section.settings.get("cta_link", ""),
                }
            elif section.type in ("featured-collection", "featured-products"):
                legacy["products"] = section.settings

    # Extract header/footer from section groups
    header_group = v3.section_groups.get("header")
    if header_group:
        for section in header_group.sections.values():
            if section.type == "header":
                legacy["header"] = section.settings

    footer_group = v3.section_groups.get("footer")
    if footer_group:
        for section in footer_group.sections.values():
            if section.type == "footer":
                legacy["footer"] = section.settings

    # Map external theme metadata
    if v3.external_theme:
        legacy["external_theme"] = {
            "bundle_url": v3.external_theme.bundle_url,
            "css_url": v3.external_theme.css_url,
            "mode": v3.external_theme.mode or "production",
        }

    return legacy


def normalize_legacy_to_v3(
    legacy: dict[str, Any],
    customization: dict[str, Any] | None = None,
) -> ThemeSettingsV3:
    """Upgrade V1/V2 legacy data to V3 in memory (Dual-Read).

    Called by the Next.js storefront SDK when a store hasn't been
    touched by the V3 customizer yet.
    """
    schema_ver = legacy.get("schema_version", 1)
    theme_block = legacy.get("theme", {})
    theme_id = theme_block.get("base_theme", "modern")

    # Build global settings from theme block + identity
    global_settings: dict[str, Any] = {}
    for key in ("primary_color", "secondary_color", "font_family", "logo_url"):
        if key in theme_block:
            global_settings[key] = theme_block[key]
    identity = legacy.get("identity", {})
    if identity:
        global_settings["identity"] = identity

    # Build home template from hero + products
    sections: dict[str, SectionInstance] = {}
    order: list[str] = []

    hero_data = legacy.get("hero", {})
    if hero_data:
        sections["hero_1"] = SectionInstance(
            type="hero",
            settings={
                "headline": hero_data.get("headline", ""),
                "headline_ar": hero_data.get("headline_ar", ""),
                "subtitle": hero_data.get("subtitle", ""),
                "background_image": hero_data.get("hero_image_url", ""),
                "cta_text": hero_data.get("cta_text", ""),
                "cta_link": hero_data.get("cta_link", ""),
            },
        )
        order.append("hero_1")

    products_data = legacy.get("products", {})
    if products_data:
        sections["featured_1"] = SectionInstance(
            type="featured-products",
            settings=products_data,
        )
        order.append("featured_1")

    templates: dict[str, PageTemplate] = {}
    if sections:
        templates["home"] = PageTemplate(name="Home", sections=sections, order=order)

    # Build section groups from header/footer
    section_groups: dict[str, SectionGroup] = {}
    header_data = legacy.get("header", {})
    if header_data:
        section_groups["header"] = SectionGroup(
            name="Header Group",
            sections={"header_1": SectionInstance(type="header", settings=header_data)},
            order=["header_1"],
        )
    else:
        section_groups["header"] = SectionGroup(
            name="Header Group",
            sections={"header_1": SectionInstance(type="header", settings={})},
            order=["header_1"],
        )

    footer_data = legacy.get("footer", {})
    if footer_data:
        section_groups["footer"] = SectionGroup(
            name="Footer Group",
            sections={"footer_1": SectionInstance(type="footer", settings=footer_data)},
            order=["footer_1"],
        )
    else:
        section_groups["footer"] = SectionGroup(
            name="Footer Group",
            sections={"footer_1": SectionInstance(type="footer", settings={})},
            order=["footer_1"],
        )

    # Handle external theme
    external_theme = None
    ext_data = legacy.get("external_theme")
    if ext_data and isinstance(ext_data, dict) and ext_data.get("bundle_url"):
        external_theme = ExternalThemeMetadata(
            bundle_url=ext_data["bundle_url"],
            css_url=ext_data.get("css_url"),
            mode=ext_data.get("mode", "production"),
        )

    return ThemeSettingsV3(
        schema_version=3,
        theme_id=theme_id,
        global_settings=global_settings,
        templates=templates,
        section_groups=section_groups,
        external_theme=external_theme,
    )


def resolve_theme_settings(
    customization_v3: dict[str, Any] | None,
    legacy_settings: dict[str, Any] | None,
    legacy_customization: dict[str, Any] | None = None,
) -> ThemeSettingsV3:
    """Pick the best available data and return a V3 payload.

    Priority: V3 > V2 legacy > V1 legacy > empty default.
    """
    # 1. If V3 data exists and is non-empty, use it
    if customization_v3 and customization_v3.get("schema_version") == 3:
        return ThemeSettingsV3(**customization_v3)

    # 2. If legacy settings exist, normalize to V3
    if legacy_settings and isinstance(legacy_settings, dict) and legacy_settings:
        return normalize_legacy_to_v3(legacy_settings, legacy_customization)

    # 3. Empty default
    return ThemeSettingsV3(
        theme_id="modern",
        global_settings={},
        templates={},
        section_groups={
            "header": SectionGroup(
                name="Header Group",
                sections={"header_1": SectionInstance(type="header", settings={})},
                order=["header_1"],
            ),
            "footer": SectionGroup(
                name="Footer Group",
                sections={"footer_1": SectionInstance(type="footer", settings={})},
                order=["footer_1"],
            ),
        },
    )

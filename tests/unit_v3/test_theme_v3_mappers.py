"""Unit tests for Dual-Write mappers.

Tests V3→Legacy backward compat, Legacy→V3 forward normalization,
and the resolve_theme_settings priority logic.
"""

import pytest

from src.core.entities.theme_settings_v3 import (
    BlockInstance,
    ExternalThemeMetadata,
    PageTemplate,
    SectionGroup,
    SectionInstance,
    ThemeSettingsV3,
)
from src.application.services.theme_v3_mappers import (
    map_v3_to_legacy_store_settings,
    normalize_legacy_to_v3,
    resolve_theme_settings,
)


# ─── V3 → Legacy (Backward Compat) ───────────────────────────────────────────


class TestMapV3ToLegacy:
    def test_basic_theme_id_mapping(self):
        v3 = ThemeSettingsV3(theme_id="bazar", global_settings={"primary_color": "#ff0000"})
        legacy = map_v3_to_legacy_store_settings(v3)
        assert legacy["theme"]["base_theme"] == "bazar"
        assert legacy["schema_version"] == 2

    def test_hero_extraction(self):
        v3 = ThemeSettingsV3(
            theme_id="bazar",
            templates={
                "home": PageTemplate(
                    name="Home",
                    sections={
                        "hero_1": SectionInstance(
                            type="hero",
                            settings={
                                "headline": "Welcome",
                                "headline_ar": "مرحبا",
                                "subtitle": "Best store",
                                "background_image": "https://img.com/hero.jpg",
                                "cta_text": "Shop Now",
                                "cta_link": "/products",
                            },
                        ),
                    },
                    order=["hero_1"],
                ),
            },
        )
        legacy = map_v3_to_legacy_store_settings(v3)
        assert legacy["hero"]["headline"] == "Welcome"
        assert legacy["hero"]["headline_ar"] == "مرحبا"
        assert legacy["hero"]["hero_image_url"] == "https://img.com/hero.jpg"

    def test_header_footer_from_section_groups(self):
        v3 = ThemeSettingsV3(
            theme_id="bazar",
            section_groups={
                "header": SectionGroup(
                    name="Header",
                    sections={"header_1": SectionInstance(type="header", settings={"logo": "logo.png"})},
                    order=["header_1"],
                ),
                "footer": SectionGroup(
                    name="Footer",
                    sections={"footer_1": SectionInstance(type="footer", settings={"copyright": "2026"})},
                    order=["footer_1"],
                ),
            },
        )
        legacy = map_v3_to_legacy_store_settings(v3)
        assert legacy["header"]["logo"] == "logo.png"
        assert legacy["footer"]["copyright"] == "2026"

    def test_external_theme_mapping(self):
        v3 = ThemeSettingsV3(
            theme_id="custom-theme",
            external_theme=ExternalThemeMetadata(
                bundle_url="https://cdn.example.com/theme.js",
                css_url="https://cdn.example.com/theme.css",
                mode="production",
            ),
        )
        legacy = map_v3_to_legacy_store_settings(v3)
        assert legacy["external_theme"]["bundle_url"] == "https://cdn.example.com/theme.js"
        assert legacy["external_theme"]["mode"] == "production"

    def test_empty_v3_produces_minimal_legacy(self):
        v3 = ThemeSettingsV3(theme_id="modern")
        legacy = map_v3_to_legacy_store_settings(v3)
        assert legacy["theme"]["base_theme"] == "modern"
        assert "hero" not in legacy
        assert "header" not in legacy


# ─── Legacy → V3 (Forward Normalization) ─────────────────────────────────────


class TestNormalizeLegacyToV3:
    def test_v1_basic_normalization(self):
        legacy = {
            "schema_version": 1,
            "theme": {"base_theme": "modern", "primary_color": "#333"},
            "hero": {"headline": "Hello", "hero_image_url": "https://img.com/h.jpg"},
        }
        v3 = normalize_legacy_to_v3(legacy)
        assert v3.schema_version == 3
        assert v3.theme_id == "modern"
        assert v3.global_settings["primary_color"] == "#333"
        assert "home" in v3.templates
        assert "hero_1" in v3.templates["home"].sections
        assert v3.templates["home"].sections["hero_1"].settings["headline"] == "Hello"

    def test_v2_with_header_footer(self):
        legacy = {
            "schema_version": 2,
            "theme": {"base_theme": "bazar"},
            "header": {"logo": "logo.png", "menu_items": []},
            "footer": {"copyright": "NUMU 2026"},
        }
        v3 = normalize_legacy_to_v3(legacy)
        assert "header" in v3.section_groups
        assert v3.section_groups["header"].sections["header_1"].settings["logo"] == "logo.png"
        assert "footer" in v3.section_groups
        assert v3.section_groups["footer"].sections["footer_1"].settings["copyright"] == "NUMU 2026"

    def test_default_section_groups_created(self):
        legacy = {"schema_version": 1, "theme": {"base_theme": "modern"}}
        v3 = normalize_legacy_to_v3(legacy)
        assert "header" in v3.section_groups
        assert "footer" in v3.section_groups
        assert v3.section_groups["header"].sections["header_1"].type == "header"

    def test_external_theme_preserved(self):
        legacy = {
            "theme": {"base_theme": "custom"},
            "external_theme": {
                "bundle_url": "https://cdn.example.com/theme.js",
                "css_url": "https://cdn.example.com/theme.css",
                "mode": "production",
            },
        }
        v3 = normalize_legacy_to_v3(legacy)
        assert v3.external_theme is not None
        assert v3.external_theme.bundle_url == "https://cdn.example.com/theme.js"

    def test_products_section_created(self):
        legacy = {
            "theme": {"base_theme": "bazar"},
            "products": {"collection_id": "abc", "limit": 8},
        }
        v3 = normalize_legacy_to_v3(legacy)
        assert "featured_1" in v3.templates["home"].sections
        assert v3.templates["home"].sections["featured_1"].type == "featured-products"

    def test_empty_legacy_produces_defaults(self):
        v3 = normalize_legacy_to_v3({})
        assert v3.theme_id == "modern"
        assert "header" in v3.section_groups
        assert "footer" in v3.section_groups


# ─── resolve_theme_settings (Priority Logic) ─────────────────────────────────


class TestResolveThemeSettings:
    def test_v3_takes_priority(self):
        v3_data = {
            "schema_version": 3,
            "theme_id": "bazar",
            "global_settings": {"primary_color": "#ff0000"},
            "templates": {},
            "section_groups": {},
        }
        legacy = {"theme": {"base_theme": "modern"}}
        result = resolve_theme_settings(v3_data, legacy)
        assert result.theme_id == "bazar"
        assert result.global_settings["primary_color"] == "#ff0000"

    def test_falls_back_to_legacy_when_v3_is_none(self):
        legacy = {
            "schema_version": 2,
            "theme": {"base_theme": "modern"},
            "hero": {"headline": "Fallback"},
        }
        result = resolve_theme_settings(None, legacy)
        assert result.theme_id == "modern"
        assert result.templates["home"].sections["hero_1"].settings["headline"] == "Fallback"

    def test_falls_back_to_legacy_when_v3_is_empty(self):
        result = resolve_theme_settings({}, {"theme": {"base_theme": "bazar"}})
        assert result.theme_id == "bazar"

    def test_returns_default_when_both_empty(self):
        result = resolve_theme_settings(None, None)
        assert result.theme_id == "modern"
        assert "header" in result.section_groups
        assert "footer" in result.section_groups

    def test_v3_with_wrong_schema_version_falls_to_legacy(self):
        v3_data = {"schema_version": 2, "theme_id": "bazar"}
        legacy = {"theme": {"base_theme": "modern"}, "hero": {"headline": "Legacy"}}
        result = resolve_theme_settings(v3_data, legacy)
        # Should fall back to legacy because schema_version != 3
        assert result.theme_id == "modern"


# ─── Roundtrip: V3 → Legacy → V3 ────────────────────────────────────────────


class TestRoundtrip:
    def test_v3_to_legacy_to_v3_preserves_core_data(self):
        """The most critical test: V3 → Legacy → V3 must not lose data."""
        original = ThemeSettingsV3(
            theme_id="bazar",
            global_settings={"primary_color": "#ff0000"},
            templates={
                "home": PageTemplate(
                    name="Home",
                    sections={
                        "hero_1": SectionInstance(
                            type="hero",
                            settings={
                                "headline": "Welcome to NUMU",
                                "headline_ar": "مرحبا بكم في نومو",
                                "subtitle": "The best store",
                                "background_image": "https://img.com/hero.jpg",
                                "cta_text": "Shop",
                                "cta_link": "/products",
                            },
                        ),
                    },
                    order=["hero_1"],
                ),
            },
            section_groups={
                "header": SectionGroup(
                    name="Header",
                    sections={"header_1": SectionInstance(type="header", settings={"logo": "logo.png"})},
                    order=["header_1"],
                ),
                "footer": SectionGroup(
                    name="Footer",
                    sections={"footer_1": SectionInstance(type="footer", settings={"copyright": "2026"})},
                    order=["footer_1"],
                ),
            },
        )

        # V3 → Legacy
        legacy = map_v3_to_legacy_store_settings(original)

        # Legacy → V3
        restored = normalize_legacy_to_v3(legacy)

        # Core data preserved
        assert restored.theme_id == "bazar"
        assert restored.templates["home"].sections["hero_1"].settings["headline"] == "Welcome to NUMU"
        assert restored.section_groups["header"].sections["header_1"].settings["logo"] == "logo.png"
        assert restored.section_groups["footer"].sections["footer_1"].settings["copyright"] == "2026"

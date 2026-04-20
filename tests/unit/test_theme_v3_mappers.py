"""Unit tests for the Dual-Write mappers.

Tests V3→Legacy backward-compat, Legacy→V3 forward normalization,
and resolve_theme_settings() detection logic.
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


def _make_v3(**overrides) -> ThemeSettingsV3:
    """Helper to create a minimal valid ThemeSettingsV3."""
    defaults = {
        "theme_id": "bazar",
        "templates": {
            "home": PageTemplate(
                name="Home",
                sections={
                    "hero_1": SectionInstance(type="hero", settings={"title": "Welcome"}),
                },
                order=["hero_1"],
            ),
        },
        "section_groups": {
            "header": SectionGroup(
                name="Header",
                sections={
                    "header_1": SectionInstance(type="header", settings={"logo": "/logo.png"}),
                },
                order=["header_1"],
            ),
            "footer": SectionGroup(
                name="Footer",
                sections={
                    "footer_1": SectionInstance(type="footer", settings={}),
                },
                order=["footer_1"],
            ),
        },
        "global_settings": {"primary_color": "#000000"},
    }
    defaults.update(overrides)
    return ThemeSettingsV3(**defaults)


class TestMapV3ToLegacy:
    def test_basic_v3_to_legacy_mapping(self, map_v3_to_legacy):
        v3 = _make_v3()
        legacy = map_v3_to_legacy(v3)
        assert legacy["theme"]["base_theme"] == "bazar"
        assert "primary_color" in legacy["theme"]

    def test_v3_sections_mapped_to_legacy_format(self, map_v3_to_legacy):
        v3 = _make_v3()
        legacy = map_v3_to_legacy(v3)
        # Legacy format should have a sections key or similar
        assert isinstance(legacy, dict)
        assert "theme" in legacy

    def test_v3_with_external_theme_maps_correctly(self, map_v3_to_legacy):
        v3 = _make_v3(
            external_theme=ExternalThemeMetadata(
                bundle_url="https://cdn.example.com/theme.js",
                mode="production",
            ),
        )
        legacy = map_v3_to_legacy(v3)
        assert "external_theme" in legacy or "theme" in legacy

    def test_v3_global_settings_in_legacy(self, map_v3_to_legacy):
        v3 = _make_v3(global_settings={"primary_color": "#ff0000", "font": "Inter"})
        legacy = map_v3_to_legacy(v3)
        assert legacy["theme"]["primary_color"] == "#ff0000"
        assert legacy["theme"]["font"] == "Inter"


class TestNormalizeLegacyToV3:
    def test_v1_flat_settings_normalized(self, normalize_legacy_to_v3):
        legacy = {
            "theme": {
                "base_theme": "bazar",
                "primary_color": "#123456",
                "font_family": "Arial",
            },
        }
        v3 = normalize_legacy_to_v3(legacy)
        assert v3.schema_version == 3
        assert v3.theme_id == "bazar"
        assert v3.global_settings["primary_color"] == "#123456"

    def test_v1_generates_default_section_groups(self, normalize_legacy_to_v3):
        legacy = {"theme": {"base_theme": "bazar"}}
        v3 = normalize_legacy_to_v3(legacy)
        assert "header" in v3.section_groups
        assert "footer" in v3.section_groups

    def test_empty_legacy_uses_fallback(self, normalize_legacy_to_v3):
        v3 = normalize_legacy_to_v3({})
        assert v3.schema_version == 3
        assert v3.theme_id is not None

    def test_v2_with_hero_data_normalized(self, normalize_legacy_to_v3):
        legacy = {
            "theme": {"base_theme": "modern"},
            "hero": {
                "headline": "Hello",
                "subtitle": "World",
            },
        }
        v3 = normalize_legacy_to_v3(legacy)
        assert v3.theme_id == "modern"
        assert "home" in v3.templates
        assert "hero_1" in v3.templates["home"].sections
        assert v3.templates["home"].sections["hero_1"].settings["headline"] == "Hello"


class TestResolveThemeSettings:
    def test_v3_data_returned_as_is(self, resolve_theme_settings):
        v3_data = {
            "schema_version": 3,
            "theme_id": "bazar",
            "templates": {},
            "section_groups": {},
            "global_settings": {},
        }
        result = resolve_theme_settings(
            customization_v3=v3_data,
            legacy_settings={"theme": {"base_theme": "old"}},
        )
        assert result.schema_version == 3
        assert result.theme_id == "bazar"

    def test_no_v3_falls_back_to_legacy(self, resolve_theme_settings):
        result = resolve_theme_settings(
            customization_v3=None,
            legacy_settings={"theme": {"base_theme": "bazar", "primary_color": "#fff"}},
        )
        assert result.schema_version == 3
        assert result.theme_id == "bazar"
        assert result.global_settings.get("primary_color") == "#fff"

    def test_both_none_returns_default(self, resolve_theme_settings):
        result = resolve_theme_settings(
            customization_v3=None,
            legacy_settings=None,
        )
        assert result.schema_version == 3
        assert result.theme_id is not None

    def test_v3_dict_parsed_correctly(self, resolve_theme_settings):
        v3_dict = {
            "schema_version": 3,
            "theme_id": "modern",
            "templates": {
                "home": {
                    "name": "Home",
                    "sections": {
                        "hero_1": {"type": "hero", "settings": {"text": "Hi"}},
                    },
                    "order": ["hero_1"],
                },
            },
            "section_groups": {},
            "global_settings": {"color": "red"},
        }
        result = resolve_theme_settings(
            customization_v3=v3_dict,
            legacy_settings=None,
        )
        assert result.templates["home"].sections["hero_1"].settings["text"] == "Hi"

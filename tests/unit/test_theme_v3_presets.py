"""Unit tests for generate_initial_v3_customization.

Tests BYOT preset generation, built-in theme defaults, and section group creation.
"""

from src.application.services.theme_v3_presets import (
    generate_initial_v3_customization,
)


class TestBuiltInThemeDefaults:
    def test_built_in_theme_generates_home_template(self):
        v3 = generate_initial_v3_customization(theme_id="bazar")
        assert "home" in v3.templates
        assert "hero_1" in v3.templates["home"].sections
        assert "featured_1" in v3.templates["home"].sections
        assert v3.templates["home"].order == ["hero_1", "featured_1"]

    def test_built_in_theme_has_default_section_groups(self):
        v3 = generate_initial_v3_customization(theme_id="modern")
        assert "header" in v3.section_groups
        assert "footer" in v3.section_groups
        assert v3.section_groups["header"].sections["header_1"].type == "header"

    def test_built_in_theme_no_external_metadata(self):
        v3 = generate_initial_v3_customization(theme_id="bazar")
        assert v3.external_theme is None


class TestByotPresets:
    def test_byot_with_presets_generates_templates(self):
        presets = {
            "templates": {
                "home": {
                    "name": "Home Page",
                    "sections": [
                        {"type": "hero", "settings": {"headline": "Welcome"}},
                        {"type": "featured-products", "settings": {"limit": 8}},
                    ],
                },
            },
            "section_groups": {
                "header": {
                    "name": "Header",
                    "sections": [{"type": "header", "settings": {"logo": "logo.png"}}],
                },
            },
        }
        v3 = generate_initial_v3_customization(
            theme_id="custom-theme",
            presets=presets,
            bundle_url="https://cdn.numueg.app/themes/custom/theme.js",
        )
        assert "home" in v3.templates
        assert len(v3.templates["home"].sections) == 2
        assert v3.templates["home"].sections["hero-0"].settings["headline"] == "Welcome"
        assert v3.external_theme is not None
        assert (
            v3.external_theme.bundle_url
            == "https://cdn.numueg.app/themes/custom/theme.js"
        )

    def test_byot_presets_with_blocks(self):
        presets = {
            "templates": {
                "home": {
                    "name": "Home",
                    "sections": [
                        {
                            "type": "rich-text",
                            "settings": {},
                            "blocks": [
                                {"type": "heading", "settings": {"text": "Title"}},
                                {
                                    "type": "paragraph",
                                    "settings": {"text": "Body text"},
                                },
                            ],
                        },
                    ],
                },
            },
        }
        v3 = generate_initial_v3_customization(theme_id="custom", presets=presets)
        section = v3.templates["home"].sections["rich-text-0"]
        assert len(section.blocks) == 2
        # Section + block ids use the shared `<type>-<idx>` (0-based) scheme so
        # the editor draft and the storefront preview derive identical ids from
        # the same preset (see theme_v3_presets._build_sections_from_list).
        assert section.block_order == ["heading-0", "paragraph-1"]
        assert section.blocks["heading-0"].settings["text"] == "Title"

    def test_byot_missing_header_gets_default(self):
        presets = {
            "templates": {"home": {"sections": [{"type": "hero", "settings": {}}]}},
        }
        v3 = generate_initial_v3_customization(theme_id="custom", presets=presets)
        assert "header" in v3.section_groups
        assert "footer" in v3.section_groups

    def test_byot_with_settings_schema_extracts_defaults(self):
        presets = {"templates": {"home": {"sections": []}}}
        settings_schema = [
            {"id": "primary_color", "type": "color", "default": "#ff0000"},
            {"id": "font_family", "type": "select", "default": "Inter"},
            {"id": "no_default", "type": "text"},
        ]
        v3 = generate_initial_v3_customization(
            theme_id="custom",
            presets=presets,
            settings_schema=settings_schema,
        )
        assert v3.global_settings["primary_color"] == "#ff0000"
        assert v3.global_settings["font_family"] == "Inter"
        assert "no_default" not in v3.global_settings

    def test_byot_external_metadata_with_css(self):
        v3 = generate_initial_v3_customization(
            theme_id="custom",
            presets={"templates": {}},
            bundle_url="https://cdn.numueg.app/themes/custom/theme.js",
            css_url="https://cdn.numueg.app/themes/custom/theme.css",
        )
        assert (
            v3.external_theme.css_url
            == "https://cdn.numueg.app/themes/custom/theme.css"
        )


class TestEdgeCases:
    def test_empty_presets_still_generates_section_groups(self):
        v3 = generate_initial_v3_customization(theme_id="custom", presets={})
        assert "header" in v3.section_groups
        assert "footer" in v3.section_groups

    def test_presets_with_empty_templates(self):
        v3 = generate_initial_v3_customization(
            theme_id="custom",
            presets={"templates": {}},
        )
        assert v3.templates == {}
        assert "header" in v3.section_groups

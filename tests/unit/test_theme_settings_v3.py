"""Unit tests for ThemeSettingsV3 Pydantic models.

Tests validation rules, defaults, and serialization.
"""

import pytest
from pydantic import ValidationError

from src.core.entities.theme_settings_v3 import (
    BlockInstance,
    ExternalThemeMetadata,
    PageTemplate,
    SectionGroup,
    SectionInstance,
    ThemeSettingsV3,
)

# ─── BlockInstance ────────────────────────────────────────────────────────────


class TestBlockInstance:
    def test_basic_block(self):
        block = BlockInstance(type="heading", settings={"text": "Hello"})
        assert block.type == "heading"
        assert block.settings == {"text": "Hello"}
        assert block.disabled is False

    def test_disabled_block(self):
        block = BlockInstance(type="heading", disabled=True)
        assert block.disabled is True

    def test_app_block_valid(self):
        block = BlockInstance(type="@app/reviews/star-rating")
        assert block.type == "@app/reviews/star-rating"

    def test_app_block_invalid_format(self):
        with pytest.raises(ValidationError, match="@app block type must be"):
            BlockInstance(type="@app/reviews")

    def test_app_block_only_prefix(self):
        with pytest.raises(ValidationError, match="@app block type must be"):
            BlockInstance(type="@app/")

    def test_empty_settings_default(self):
        block = BlockInstance(type="divider")
        assert block.settings == {}


# ─── SectionInstance ──────────────────────────────────────────────────────────


class TestSectionInstance:
    def test_basic_section(self):
        section = SectionInstance(type="hero", settings={"headline": "Welcome"})
        assert section.type == "hero"
        assert section.blocks == {}
        assert section.block_order == []

    def test_section_with_blocks(self):
        section = SectionInstance(
            type="rich-text",
            blocks={
                "heading_1": BlockInstance(type="heading", settings={"text": "Title"}),
                "paragraph_1": BlockInstance(
                    type="paragraph", settings={"text": "Body"}
                ),
            },
            block_order=["heading_1", "paragraph_1"],
        )
        assert len(section.blocks) == 2
        assert section.block_order == ["heading_1", "paragraph_1"]

    def test_disabled_section(self):
        section = SectionInstance(type="hero", disabled=True)
        assert section.disabled is True


# ─── PageTemplate ─────────────────────────────────────────────────────────────


class TestPageTemplate:
    def test_basic_template(self):
        tpl = PageTemplate(
            name="Home",
            sections={
                "hero_1": SectionInstance(type="hero", settings={}),
            },
            order=["hero_1"],
        )
        assert tpl.name == "Home"
        assert len(tpl.sections) == 1
        assert tpl.order == ["hero_1"]

    def test_empty_template(self):
        tpl = PageTemplate(name="Empty")
        assert tpl.sections == {}
        assert tpl.order == []


# ─── SectionGroup ─────────────────────────────────────────────────────────────


class TestSectionGroup:
    def test_header_group(self):
        group = SectionGroup(
            name="Header Group",
            sections={
                "announcement_1": SectionInstance(
                    type="announcement-bar", settings={"text": "Sale!"}
                ),
                "header_1": SectionInstance(type="header", settings={}),
            },
            order=["announcement_1", "header_1"],
        )
        assert group.name == "Header Group"
        assert len(group.sections) == 2
        assert group.order[0] == "announcement_1"


# ─── ExternalThemeMetadata ────────────────────────────────────────────────────


class TestExternalThemeMetadata:
    def test_production_mode(self):
        meta = ExternalThemeMetadata(
            bundle_url="https://cdn.numueg.app/themes/example/theme.js"
        )
        assert meta.mode == "production"
        assert meta.css_url is None
        assert meta.dev_url is None

    def test_development_mode(self):
        meta = ExternalThemeMetadata(
            bundle_url="http://localhost:5173/theme.js",
            mode="development",
            dev_url="http://localhost:5173",
        )
        assert meta.mode == "development"

    def test_invalid_mode(self):
        with pytest.raises(ValidationError):
            ExternalThemeMetadata(
                bundle_url="https://cdn.numueg.app/themes/example/theme.js",
                mode="staging",
            )

    def test_bundle_url_must_be_on_allowlist(self):
        # Arbitrary HTTPS host is rejected — prevents tenant-side script injection.
        with pytest.raises(ValidationError, match="not on the allowlist"):
            ExternalThemeMetadata(bundle_url="https://attacker.example/evil.js")

    def test_bundle_url_http_rejected_in_production(self):
        with pytest.raises(ValidationError, match="not on the allowlist"):
            ExternalThemeMetadata(bundle_url="http://cdn.numueg.app/themes/x/theme.js")

    def test_dev_mode_allows_localhost(self):
        meta = ExternalThemeMetadata(
            bundle_url="http://localhost:5173/theme.js",
            mode="development",
        )
        assert meta.bundle_url.startswith("http://localhost")

    def test_dev_mode_rejects_external_host(self):
        with pytest.raises(ValidationError, match="not on the allowlist"):
            ExternalThemeMetadata(
                bundle_url="http://attacker.example/theme.js",
                mode="development",
            )

    def test_css_url_must_be_on_allowlist(self):
        with pytest.raises(ValidationError, match="css_url"):
            ExternalThemeMetadata(
                bundle_url="https://cdn.numueg.app/themes/x/theme.js",
                css_url="https://attacker.example/evil.css",
            )


# ─── ThemeSettingsV3 ──────────────────────────────────────────────────────────


class TestThemeSettingsV3:
    def test_minimal_v3(self):
        v3 = ThemeSettingsV3(theme_id="bazar")
        assert v3.schema_version == 3
        assert v3.theme_id == "bazar"
        assert v3.global_settings == {}
        assert v3.templates == {}
        assert v3.section_groups == {}
        assert v3.external_theme is None

    def test_full_v3(self):
        v3 = ThemeSettingsV3(
            theme_id="bazar",
            global_settings={"primary_color": "#ff0000"},
            templates={
                "home": PageTemplate(
                    name="Home",
                    sections={"hero_1": SectionInstance(type="hero", settings={})},
                    order=["hero_1"],
                ),
            },
            section_groups={
                "header": SectionGroup(
                    name="Header Group",
                    sections={"header_1": SectionInstance(type="header", settings={})},
                    order=["header_1"],
                ),
            },
        )
        assert "home" in v3.templates
        assert "header" in v3.section_groups

    def test_schema_version_locked_to_3(self):
        with pytest.raises(ValidationError):
            ThemeSettingsV3(schema_version=2, theme_id="bazar")

    def test_serialization_roundtrip(self):
        v3 = ThemeSettingsV3(
            theme_id="bazar",
            templates={
                "home": PageTemplate(
                    name="Home",
                    sections={
                        "hero_1": SectionInstance(
                            type="hero",
                            settings={"headline": "Welcome"},
                            blocks={
                                "btn_1": BlockInstance(
                                    type="button", settings={"text": "Shop Now"}
                                ),
                            },
                            block_order=["btn_1"],
                        ),
                    },
                    order=["hero_1"],
                ),
            },
        )
        data = v3.model_dump()
        restored = ThemeSettingsV3(**data)
        assert (
            restored.templates["home"]
            .sections["hero_1"]
            .blocks["btn_1"]
            .settings["text"]
            == "Shop Now"
        )

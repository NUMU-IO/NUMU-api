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


# ─── Nested blocks (Phase 4.1 keystone) ────────────────────────────────────────


class TestNestedBlockRoundtrip:
    """``BlockInstance`` is self-referential, so a block can hold child
    ``blocks`` to arbitrary depth. This is the KEYSTONE of Phase 4.1: the
    autosave path does ``ThemeSettingsV3(**payload).model_dump()`` — a
    NON-recursive model would silently strip the nested ``blocks`` /
    ``block_order`` on every save, so these tests lock the recursion in."""

    def test_block_nests_recursively(self):
        block = BlockInstance(
            type="column",
            settings={"heading": "Shop"},
            blocks={"link_1": BlockInstance(type="link", settings={"url": "/x"})},
            block_order=["link_1"],
        )
        assert block.blocks["link_1"].type == "link"
        assert block.block_order == ["link_1"]

    def test_block_roundtrips_dump_then_validate(self):
        block = BlockInstance(
            type="column",
            blocks={
                "link_1": BlockInstance(
                    type="link",
                    settings={"url": "/products"},
                    blocks={
                        "icon_1": BlockInstance(type="icon", settings={"name": "cart"})
                    },
                    block_order=["icon_1"],
                ),
            },
            block_order=["link_1"],
        )
        restored = BlockInstance(**block.model_dump())
        link = restored.blocks["link_1"]
        assert link.block_order == ["icon_1"]
        assert link.blocks["icon_1"].settings["name"] == "cart"

    def test_deeply_nested_blocks_survive_themesettings_roundtrip(self):
        v3 = ThemeSettingsV3(
            theme_id="bon-younes-v3",
            templates={
                "home": PageTemplate(
                    name="Home",
                    order=["footer_1"],
                    sections={
                        "footer_1": SectionInstance(
                            type="by-footer",
                            block_order=["col_1"],
                            blocks={
                                "col_1": BlockInstance(
                                    type="column",
                                    settings={"heading": "Shop"},
                                    block_order=["link_1"],
                                    blocks={
                                        "link_1": BlockInstance(
                                            type="link",
                                            settings={
                                                "url": "/products",
                                                "label": "All",
                                            },
                                        ),
                                    },
                                ),
                            },
                        ),
                    },
                ),
            },
        )
        # Mirror the autosave path exactly (theme_v3_service).
        restored = ThemeSettingsV3(**v3.model_dump())
        col = restored.templates["home"].sections["footer_1"].blocks["col_1"]
        assert col.type == "column"
        assert col.block_order == ["link_1"]
        link = col.blocks["link_1"]
        assert link.type == "link"
        assert link.settings["url"] == "/products"

    def test_raw_json_dict_preserves_nesting(self):
        # The autosave PUT body arrives as JSON → a plain dict. Validating
        # it must preserve nested blocks (no recursion → they'd be dropped).
        payload = {
            "schema_version": 3,
            "theme_id": "x",
            "templates": {
                "home": {
                    "name": "Home",
                    "order": ["s1"],
                    "sections": {
                        "s1": {
                            "type": "rich-text",
                            "block_order": ["b1"],
                            "blocks": {
                                "b1": {
                                    "type": "group",
                                    "block_order": ["b2"],
                                    "blocks": {
                                        "b2": {"type": "text", "settings": {"t": "hi"}},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        v3 = ThemeSettingsV3(**payload)
        b1 = v3.templates["home"].sections["s1"].blocks["b1"]
        assert b1.blocks["b2"].settings["t"] == "hi"
        dumped = v3.model_dump()
        assert (
            dumped["templates"]["home"]["sections"]["s1"]["blocks"]["b1"]["blocks"][
                "b2"
            ]["type"]
            == "text"
        )

    def test_leaf_block_back_compat(self):
        # A one-level block from before the recursive change (no blocks key)
        # validates and gets empty defaults — additive, no migration needed.
        b = BlockInstance(**{"type": "heading", "settings": {"text": "Hi"}})
        assert b.blocks == {}
        assert b.block_order == []

    def test_app_block_validation_fires_on_nested_block(self):
        # @app block-type validation still runs when the bad block is nested.
        with pytest.raises(ValidationError, match="@app block type must be"):
            BlockInstance(**{
                "type": "column",
                "block_order": ["bad"],
                "blocks": {"bad": {"type": "@app/x"}},
            })

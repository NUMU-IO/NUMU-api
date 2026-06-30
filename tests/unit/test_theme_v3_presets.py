"""Unit tests for generate_initial_v3_customization.

Tests BYOT preset generation, built-in theme defaults, and section group creation.
"""

from src.application.services.theme_v3_presets import (
    _known_section_types,
    generate_initial_v3_customization,
    reconcile_v3_customization,
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


class TestByotChromeGroups:
    """Phase 2.5 — BYOT bundles render chrome in-template, so the generic
    header/footer groups must NOT be synthesised (they'd be un-editable
    phantom tiles). The signal is `bundle_url` (a real BYOT bundle)."""

    def test_byot_bundle_no_preset_groups_skips_phantom_groups(self):
        presets = {
            "templates": {"home": {"sections": [{"type": "by-header", "settings": {}}]}}
        }
        v3 = generate_initial_v3_customization(
            theme_id="bon-younes-v3",
            presets=presets,
            bundle_url="https://cdn.numueg.app/themes/by/theme.js",
        )
        assert v3.section_groups == {}

    def test_byot_bundle_keeps_declared_preset_groups(self):
        presets = {
            "templates": {"home": {"sections": []}},
            "section_groups": {
                "header": {"sections": [{"type": "header", "settings": {}}]}
            },
        }
        v3 = generate_initial_v3_customization(
            theme_id="custom",
            presets=presets,
            bundle_url="https://cdn.numueg.app/themes/custom/theme.js",
        )
        assert "header" in v3.section_groups
        # Footer was not declared and the theme is BYOT → not synthesised.
        assert "footer" not in v3.section_groups


class TestReconcileChromeGroups:
    """Phase 2.5 — reconcile clears phantom groups for in-template themes and
    never resurrects a group the theme's presets don't declare."""

    def test_reconcile_clears_phantom_groups_for_in_template_theme(self):
        # A store activated before Phase 2.5 still carries generic
        # header/footer groups. With NO preset section_groups (in-template),
        # reconcile must clear them.
        customization = {
            "schema_version": 3,
            "templates": {
                "home": {
                    "sections": {"by-header-0": {"type": "by-header"}},
                    "order": ["by-header-0"],
                }
            },
            "section_groups": {
                "header": {
                    "sections": {"header_1": {"type": "header"}},
                    "order": ["header_1"],
                },
                "footer": {
                    "sections": {"footer_1": {"type": "footer"}},
                    "order": ["footer_1"],
                },
            },
        }
        section_schemas = {"by-header": {"name": "Header", "settings": []}}
        presets = {
            "templates": {"home": {"sections": [{"type": "by-header", "settings": {}}]}}
        }
        result = reconcile_v3_customization(customization, section_schemas, presets)
        assert result["section_groups"] == {}

    def test_reconcile_keeps_groups_for_group_based_theme(self):
        # A theme that declares preset groups whose stored sections are
        # renderable is left untouched (no clobber).
        customization = {
            "schema_version": 3,
            "templates": {
                "home": {
                    "sections": {"hero-0": {"type": "hero"}},
                    "order": ["hero-0"],
                }
            },
            "section_groups": {
                "header": {
                    "sections": {"h1": {"type": "site-header"}},
                    "order": ["h1"],
                }
            },
        }
        section_schemas = {"hero": {}, "site-header": {}}
        presets = {
            "templates": {"home": {"sections": [{"type": "hero", "settings": {}}]}},
            "section_groups": {
                "header": {"sections": [{"type": "site-header", "settings": {}}]}
            },
        }
        result = reconcile_v3_customization(customization, section_schemas, presets)
        groups = result.get("section_groups") if result else None
        assert groups and "header" in groups


class TestReconcileTemplates:
    """Core reconcile contract: heal empty/stale templates from the theme's
    preset, but never clobber a template that has at least one renderable
    section (preserves real merchant edits)."""

    PRESETS = {
        "templates": {
            "home": {
                "name": "Home",
                "sections": [
                    {"type": "by-hero", "settings": {"headline": "Hi"}},
                    {"type": "by-grid", "settings": {}},
                ],
            }
        }
    }
    SCHEMAS = {"by-hero": {}, "by-grid": {}}

    def test_empty_template_healed_from_preset(self):
        cust = {
            "schema_version": 3,
            "templates": {"home": {"sections": {}, "order": []}},
        }
        out = reconcile_v3_customization(cust, self.SCHEMAS, self.PRESETS)
        secs = out["templates"]["home"]["sections"]
        # Deterministic <type>-<idx> ids (must match storefront/preview).
        assert "by-hero-0" in secs and "by-grid-1" in secs
        assert out["templates"]["home"]["order"] == ["by-hero-0", "by-grid-1"]

    def test_all_unknown_template_replaced(self):
        # A stale `legacy-hero` from a previous theme — unknown to this
        # theme's schemas → replaced by the preset.
        cust = {
            "schema_version": 3,
            "templates": {
                "home": {
                    "sections": {"hero_1": {"type": "legacy-hero"}},
                    "order": ["hero_1"],
                }
            },
        }
        out = reconcile_v3_customization(cust, self.SCHEMAS, self.PRESETS)
        assert "hero_1" not in out["templates"]["home"]["sections"]
        assert "by-hero-0" in out["templates"]["home"]["sections"]

    def test_partial_known_template_kept_untouched(self):
        # One known + one unknown section → has a renderable section → keep
        # (no clobber). reconcile returns the SAME object (cheap no-op).
        cust = {
            "schema_version": 3,
            "templates": {
                "home": {
                    "sections": {"x": {"type": "by-hero"}, "y": {"type": "legacy"}},
                    "order": ["x", "y"],
                }
            },
        }
        out = reconcile_v3_customization(cust, self.SCHEMAS, self.PRESETS)
        assert out is cust

    def test_missing_template_is_created_from_preset(self):
        cust = {"schema_version": 3, "templates": {}}
        out = reconcile_v3_customization(cust, self.SCHEMAS, self.PRESETS)
        assert "home" in out["templates"]
        assert out["templates"]["home"]["order"] == ["by-hero-0", "by-grid-1"]

    def test_no_presets_returns_unchanged(self):
        cust = {"schema_version": 3, "templates": {"home": {"sections": {}}}}
        assert reconcile_v3_customization(cust, self.SCHEMAS, None) is cust
        assert reconcile_v3_customization(cust, self.SCHEMAS, {}) is cust

    # Regression: bundles store schemas as an ENVELOPE
    # {"sections": {<type>: ...}, "blocks": {...}}. Reading the envelope's own
    # keys ("sections"/"blocks") as the known types made every real section look
    # unrenderable, so reconcile swapped the merchant's templates for presets —
    # the editor "erased" published edits on re-open.
    ENVELOPE_SCHEMAS = {"blocks": {}, "sections": {"by-hero": {}, "by-grid": {}}}

    def test_known_section_types_unwraps_envelope(self):
        assert _known_section_types(self.ENVELOPE_SCHEMAS) == {"by-hero", "by-grid"}
        # flat map still works
        assert _known_section_types(self.SCHEMAS) == {"by-hero", "by-grid"}
        # shopify-style list still works
        assert _known_section_types([{"type": "by-hero"}, {"type": "by-grid"}]) == {
            "by-hero",
            "by-grid",
        }

    def test_envelope_schemas_keep_real_merchant_template(self):
        # With envelope schemas, a template made of real theme sections must be
        # KEPT (no clobber) — the bug swapped it for the preset.
        cust = {
            "schema_version": 3,
            "templates": {
                "home": {
                    "sections": {"a": {"type": "by-hero"}, "b": {"type": "by-grid"}},
                    "order": ["a", "b"],
                }
            },
        }
        out = reconcile_v3_customization(cust, self.ENVELOPE_SCHEMAS, self.PRESETS)
        assert out is cust  # untouched no-op

    def test_no_schema_info_keeps_existing_nonempty(self):
        # Empty `known` set → "can't judge type-compat" → keep existing.
        cust = {
            "schema_version": 3,
            "templates": {
                "home": {"sections": {"x": {"type": "anything"}}, "order": ["x"]}
            },
        }
        assert reconcile_v3_customization(cust, {}, self.PRESETS) is cust

    def test_non_dict_customization_returned_asis(self):
        assert reconcile_v3_customization(None, self.SCHEMAS, self.PRESETS) is None
        assert reconcile_v3_customization("oops", self.SCHEMAS, self.PRESETS) == "oops"

"""Tests for the theme update classifier (Phase 5.1).

Locks Shopify's manual-vs-automatic rules: a removed/retyped setting, a
tightened range, or a removed section/block forces MANUAL review; additions
and widenings are AUTOMATIC (safe to apply without losing customization).
"""

from src.application.services.theme_update_classifier import (
    classify_theme_update,
)


def _schemas(settings=None, sections=None):
    return {
        "settings_schema": settings if settings is not None else [],
        "section_schemas": sections if sections is not None else {},
    }


class TestGlobalSettings:
    def test_identical_is_automatic_no_changes(self):
        s = _schemas([{"id": "primary_color", "type": "color"}])
        out = classify_theme_update(s, s)
        assert out["classification"] == "automatic"
        assert out["breaking"] is False
        assert out["changes"] == []

    def test_added_setting_is_automatic(self):
        old = _schemas([{"id": "a", "type": "text"}])
        new = _schemas([{"id": "a", "type": "text"}, {"id": "b", "type": "text"}])
        out = classify_theme_update(old, new)
        assert out["classification"] == "automatic"
        assert any(c["kind"] == "setting_added" for c in out["changes"])

    def test_removed_setting_is_manual(self):
        old = _schemas([{"id": "a", "type": "text"}, {"id": "b", "type": "text"}])
        new = _schemas([{"id": "a", "type": "text"}])
        out = classify_theme_update(old, new)
        assert out["classification"] == "manual"
        assert any(
            c["kind"] == "setting_removed" and c["breaking"] for c in out["changes"]
        )

    def test_type_change_is_manual(self):
        old = _schemas([{"id": "a", "type": "text"}])
        new = _schemas([{"id": "a", "type": "richtext"}])
        out = classify_theme_update(old, new)
        assert out["classification"] == "manual"
        assert any(c["kind"] == "setting_type_changed" for c in out["changes"])

    def test_range_tightened_min_up_is_manual(self):
        old = _schemas([{"id": "w", "type": "range", "min": 0, "max": 100}])
        new = _schemas([{"id": "w", "type": "range", "min": 10, "max": 100}])
        out = classify_theme_update(old, new)
        assert out["classification"] == "manual"
        assert any(c["kind"] == "range_tightened" for c in out["changes"])

    def test_range_tightened_max_down_is_manual(self):
        old = _schemas([{"id": "w", "type": "range", "min": 0, "max": 100}])
        new = _schemas([{"id": "w", "type": "range", "min": 0, "max": 50}])
        out = classify_theme_update(old, new)
        assert out["classification"] == "manual"

    def test_range_widened_is_automatic(self):
        old = _schemas([{"id": "w", "type": "range", "min": 10, "max": 50}])
        new = _schemas([{"id": "w", "type": "range", "min": 0, "max": 100}])
        out = classify_theme_update(old, new)
        assert out["classification"] == "automatic"

    def test_group_style_schema_flattens(self):
        # settings_schema can be a list of {name, settings:[...]} groups.
        old = _schemas([
            {"name": "Brand", "settings": [{"id": "logo", "type": "image_picker"}]}
        ])
        new = _schemas([{"name": "Brand", "settings": []}])
        out = classify_theme_update(old, new)
        assert out["classification"] == "manual"
        assert any(c["target"] == "global.setting:logo" for c in out["changes"])

    def test_divider_changes_are_ignored(self):
        old = _schemas([
            {"type": "header", "content": "Brand"},
            {"id": "a", "type": "text"},
        ])
        new = _schemas([{"id": "a", "type": "text"}])
        out = classify_theme_update(old, new)
        # The header divider vanishing is not a data-bearing change.
        assert out["classification"] == "automatic"
        assert out["changes"] == []


class TestSectionsAndBlocks:
    def test_section_removed_is_manual(self):
        old = _schemas(sections={"hero": {"settings": []}, "grid": {"settings": []}})
        new = _schemas(sections={"hero": {"settings": []}})
        out = classify_theme_update(old, new)
        assert out["classification"] == "manual"
        assert any(c["kind"] == "section_removed" for c in out["changes"])

    def test_section_added_is_automatic(self):
        old = _schemas(sections={"hero": {"settings": []}})
        new = _schemas(sections={"hero": {"settings": []}, "grid": {"settings": []}})
        out = classify_theme_update(old, new)
        assert out["classification"] == "automatic"
        assert any(c["kind"] == "section_added" for c in out["changes"])

    def test_block_removed_is_manual(self):
        old = _schemas(
            sections={
                "footer": {
                    "settings": [],
                    "blocks": [{"type": "column"}, {"type": "link"}],
                }
            }
        )
        new = _schemas(
            sections={"footer": {"settings": [], "blocks": [{"type": "column"}]}}
        )
        out = classify_theme_update(old, new)
        assert out["classification"] == "manual"
        assert any(c["kind"] == "block_removed" for c in out["changes"])

    def test_block_added_is_automatic(self):
        old = _schemas(sections={"footer": {"blocks": [{"type": "column"}]}})
        new = _schemas(
            sections={"footer": {"blocks": [{"type": "column"}, {"type": "social"}]}}
        )
        out = classify_theme_update(old, new)
        assert out["classification"] == "automatic"

    def test_section_setting_retype_is_manual(self):
        old = _schemas(sections={"hero": {"settings": [{"id": "h", "type": "text"}]}})
        new = _schemas(sections={"hero": {"settings": [{"id": "h", "type": "number"}]}})
        out = classify_theme_update(old, new)
        assert out["classification"] == "manual"
        assert any(c["target"] == "section:hero.setting:h" for c in out["changes"])


class TestEdgeCases:
    def test_none_inputs_are_automatic(self):
        out = classify_theme_update(None, None)
        assert out["classification"] == "automatic"
        assert out["changes"] == []

    def test_first_install_no_old_schemas(self):
        new = _schemas([{"id": "a", "type": "text"}], {"hero": {"settings": []}})
        out = classify_theme_update(None, new)
        # Everything is an addition → automatic.
        assert out["classification"] == "automatic"

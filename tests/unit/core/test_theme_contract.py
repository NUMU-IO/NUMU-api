"""Tests for the server-side theme-contract gate (src/core/theme_contract.py)."""

import json

from src.core.theme_contract import (
    HOST_CONTRACT_VERSION,
    validate_built_manifest,
    validate_dist_bundle,
)

_GOOD_MANIFEST = {
    "id": "my-theme",
    "name": "My Theme",
    "version": "1.0.0",
    "author": "Me <me@example.com>",
    "plugin_version": "0.3.0",
    "section_schemas": {"hero": {"type": "hero", "name": "Hero"}},
    "presets": {"templates": {"home": {"sections": [{"type": "hero"}]}}},
}


def test_valid_built_manifest_passes():
    assert (
        validate_built_manifest(
            _GOOD_MANIFEST, {"contract_version": HOST_CONTRACT_VERSION}
        )
        == []
    )


def test_bilingual_name_object_accepted():
    m = dict(_GOOD_MANIFEST, name={"en": "Hi", "ar": "مرحبا"})
    assert validate_built_manifest(m) == []


def test_bad_id_and_version_flagged():
    errors = validate_built_manifest(dict(_GOOD_MANIFEST, id="-bad-", version="1.0"))
    joined = " ".join(errors)
    assert "id '-bad-'" in joined
    assert "not valid semver" in joined


def test_preset_references_unshipped_section_type():
    m = dict(_GOOD_MANIFEST, section_schemas={})
    errors = validate_built_manifest(m)
    assert any("preset references section type 'hero'" in e for e in errors)


def test_section_schema_filename_mismatch():
    m = dict(
        _GOOD_MANIFEST,
        section_schemas={"hero": {"type": "promo", "name": "Hero"}},
    )
    errors = validate_built_manifest(m)
    assert any("must equal its key 'hero'" in e for e in errors)


def test_future_contract_version_refused():
    errors = validate_built_manifest(
        _GOOD_MANIFEST,
        {"contract_version": HOST_CONTRACT_VERSION + 1},
        host_contract_version=HOST_CONTRACT_VERSION,
    )
    assert any("supports up to" in e for e in errors)


def test_validate_dist_bundle_reads_files(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(_GOOD_MANIFEST), encoding="utf-8"
    )
    (tmp_path / "import-map.json").write_text(
        json.dumps({"contract_version": HOST_CONTRACT_VERSION}), encoding="utf-8"
    )
    assert validate_dist_bundle(tmp_path) == []


def test_validate_dist_bundle_missing_manifest(tmp_path):
    errors = validate_dist_bundle(tmp_path)
    assert any("manifest.json not found" in e for e in errors)

"""Section library in the API (theme-section-base Phase 3).

The storefront serves the ``lib-*`` section code; the API decides which stores
see their schemas. Only a theme version whose manifest declares
``supports.section_library`` gets them, theme-owned types always win, and a
theme's ``replaces`` list hides library duplicates.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.services.marketplace_service import MarketplaceService
from src.infrastructure.repositories.store_theme_repository import (
    StoreThemeRepository,
)
from src.infrastructure.section_library import (
    SUPPORTS_KEY,
    library_sections,
    merge_section_library,
    pack_supports,
    split_supports,
)

OPT_IN = {"section_library": {"version": 1}}


def test_catalog_holds_the_library_sections():
    sections = library_sections()
    assert len(sections) == 25
    for section_type, schema in sections.items():
        assert section_type.startswith("lib-")
        assert schema["type"] == section_type
        assert schema["locales"]["ar"]["name"]


@pytest.mark.parametrize(
    "supports", [None, {}, {"section_library": False}, {"other": True}]
)
def test_versions_that_do_not_opt_in_are_unchanged(supports):
    assert merge_section_library({"hero": {"type": "hero"}}, supports) == {
        "hero": {"type": "hero"}
    }


def test_flat_map_gains_every_library_section():
    merged = merge_section_library({"hero": {"type": "hero"}}, OPT_IN)
    assert set(merged) == {"hero", *library_sections()}


def test_envelope_merges_into_sections_and_keeps_blocks():
    merged = merge_section_library(
        {"sections": {"hero": {"type": "hero"}}, "blocks": {"b": {}}}, OPT_IN
    )
    assert set(merged) == {"sections", "blocks"}
    assert set(merged["sections"]) == {"hero", *library_sections()}
    assert merged["blocks"] == {"b": {}}


def test_theme_owned_types_win_and_replaces_are_hidden():
    supports = {
        "section_library": {
            "version": 1,
            "replaces": ["lib-ugc-carousel", "lib-before-after"],
        }
    }
    merged = merge_section_library(
        {"lib-faq": {"type": "lib-faq", "name": "Our FAQ"}, "hero": {}}, supports
    )
    assert merged["lib-faq"] == {"type": "lib-faq", "name": "Our FAQ"}
    assert "lib-ugc-carousel" not in merged
    assert "lib-before-after" not in merged
    assert "lib-lookbook" in merged


def test_empty_or_list_schemas_are_left_alone():
    # An empty map means "no schema info", so reconcile keeps the merchant's
    # templates; a library-only map would make every theme section unknown.
    assert merge_section_library({}, OPT_IN) == {}
    assert merge_section_library(None, OPT_IN) is None
    assert merge_section_library([{"type": "hero"}], OPT_IN) == [{"type": "hero"}]


def test_each_merge_gets_its_own_copy_of_the_catalog():
    first = merge_section_library({"hero": {}}, OPT_IN)
    first["lib-faq"]["name"] = "changed"
    assert merge_section_library({"hero": {}}, OPT_IN)["lib-faq"]["name"] == "FAQ"


def test_supports_round_trip_through_marketplace_presets():
    presets = {"templates": {"home": {}}}
    packed = pack_supports(presets, {"supports": OPT_IN})
    assert packed == {"templates": {"home": {}}, SUPPORTS_KEY: OPT_IN}
    assert presets == {"templates": {"home": {}}}
    assert split_supports(packed) == (presets, OPT_IN)
    assert pack_supports(presets, {"id": "genova-v3"}) is presets
    assert split_supports(presets) == (presets, None)
    assert split_supports(None) == (None, None)


def _store_theme_model(manifest):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        tenant_id=uuid4(),
        store_id=uuid4(),
        theme_id=uuid4(),
        theme_version_id=uuid4(),
        is_active=True,
        name=None,
        customization={},
        draft_customization={},
        customization_v3={},
        draft_customization_v3={},
        installed_at=now,
        activated_at=now,
        theme=SimpleNamespace(
            slug="genova-v3",
            name="Genova",
            type="external",
            thumbnail_url=None,
            settings_schema={},
            section_schemas={"gn-hero": {"type": "gn-hero"}},
        ),
        theme_version=SimpleNamespace(
            version="1.1.0",
            bundle_url="https://cdn.numueg.app/genova-v3/1.1.0/theme.js",
            css_url=None,
            manifest=manifest,
        ),
    )


def test_store_theme_lists_library_sections_only_for_an_opted_in_version():
    repo = StoreThemeRepository(session=None)
    opted_in = repo._to_entity(_store_theme_model({"presets": {}, "supports": OPT_IN}))
    assert {"gn-hero", "lib-faq", "lib-store-visit"} <= set(opted_in.section_schemas)
    older = repo._to_entity(_store_theme_model({"presets": {}}))
    assert set(older.section_schemas) == {"gn-hero"}


class _StopAfterManifest(Exception):
    pass


async def test_activation_moves_supports_from_presets_into_the_runtime_manifest():
    version = SimpleNamespace(
        bundle_url="https://cdn.numueg.app/genova-v3/1.1.0/theme.js",
        css_url=None,
        settings_schema={},
        section_schemas={"gn-hero": {"type": "gn-hero"}},
        presets={"templates": {"home": {"sections": []}}, SUPPORTS_KEY: OPT_IN},
        version_string="1.1.0",
        checksum="abc123",
    )
    marketplace_repo = SimpleNamespace(
        get_installation=AsyncMock(
            return_value=SimpleNamespace(
                uninstalled_at=None,
                preview_expires_at=None,
                marketplace_version_id=uuid4(),
            )
        ),
        get_version_by_id=AsyncMock(return_value=version),
        get_theme_by_id=AsyncMock(
            return_value=SimpleNamespace(
                slug="genova-v3",
                name="Genova",
                description=None,
                supported_features=None,
            )
        ),
    )
    version_repo = SimpleNamespace(create=AsyncMock(side_effect=lambda v: v))
    service = MarketplaceService(
        marketplace_repo,
        # Stop right after the runtime version (and its manifest) is written.
        store_theme_repo=SimpleNamespace(
            get_active_for_store=AsyncMock(side_effect=_StopAfterManifest)
        ),
        store_repo=SimpleNamespace(),
        theme_repo=SimpleNamespace(
            get_by_slug=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
            update=AsyncMock(side_effect=lambda t: t),
        ),
        version_repo=version_repo,
    )

    with pytest.raises(_StopAfterManifest):
        await service.activate_theme(uuid4(), uuid4())

    manifest = version_repo.create.await_args.args[0].manifest
    assert manifest["supports"] == OPT_IN
    assert manifest["presets"] == {"templates": {"home": {"sections": []}}}

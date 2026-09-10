"""Sector preset + capability resolution checks.

Covers the three things that would silently break the feature: applying a
preset twice must not duplicate anything, an unimplemented capability must
stay off no matter what is written to the store, and a theme layout must be
filtered against the sections the active theme actually declares.
"""

from uuid import uuid4

import pytest

from src.application.services.capability_service import CapabilityService
from src.application.services.sector_preset_service import (
    SectorPresetService,
    _available_section_types,
)
from src.core.entities.store import Store
from src.core.sector_presets import SECTOR_PRESETS, get_preset


class _FakeDefinitionRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple, object] = {}

    async def get_by_key(self, store_id, owner_type, namespace, key):
        return self.rows.get((store_id, owner_type, namespace, key))

    async def create(self, entity):
        self.rows[
            (entity.store_id, entity.owner_type, entity.namespace, entity.key)
        ] = entity
        return entity


class _FakeCategoryRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple, object] = {}

    async def get_by_slug(self, store_id, slug):
        return self.rows.get((store_id, slug))

    async def create(self, entity):
        self.rows[(entity.store_id, entity.slug)] = entity
        return entity


class _FakeStoreRepo:
    def __init__(self) -> None:
        self.updates = 0

    async def update(self, entity):
        self.updates += 1
        return entity


class _FakeStoreTheme:
    def __init__(self, section_schemas):
        self.section_schemas = section_schemas
        self.customization_v3: dict = {}
        self.draft_customization_v3: dict = {}


class _FakeStoreThemeRepo:
    def __init__(self, store_theme=None) -> None:
        self.store_theme = store_theme

    async def get_active_for_store(self, store_id):
        return self.store_theme

    async def update(self, entity):
        return entity


def _store() -> Store:
    return Store(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Test Store",
        slug="test-store",
        owner_id=uuid4(),
        subdomain="test",
        settings={},
    )


def _service(store_theme=None) -> tuple[SectorPresetService, _FakeDefinitionRepo]:
    definition_repo = _FakeDefinitionRepo()
    service = SectorPresetService(
        definition_repo=definition_repo,
        category_repo=_FakeCategoryRepo(),
        store_repo=_FakeStoreRepo(),
        store_theme_repo=_FakeStoreThemeRepo(store_theme),
    )
    return service, definition_repo


@pytest.mark.asyncio
async def test_apply_is_idempotent():
    preset = get_preset("bookstore")
    assert preset is not None
    store = _store()
    service, definition_repo = _service()

    first = await service.apply(store, preset)
    assert first["fields_created"] == len(preset.fields)
    assert first["categories_created"] == len(preset.categories)

    second = await service.apply(store, preset)
    assert second["fields_created"] == 0
    assert second["fields_skipped"] == len(preset.fields)
    assert second["categories_created"] == 0
    assert len(definition_repo.rows) == len(preset.fields)


@pytest.mark.asyncio
async def test_apply_records_sector_and_enables_capabilities():
    preset = get_preset("coffee")
    assert preset is not None
    store = _store()
    service, _ = _service()

    report = await service.apply(store, preset)

    assert store.settings["sector"] == "coffee"
    for key in preset.capabilities:
        assert store.settings["capabilities"][key] is True
    assert report["capabilities_unavailable"] == []


@pytest.mark.asyncio
async def test_theme_layout_is_filtered_to_declared_sections():
    preset = get_preset("fashion")
    assert preset is not None
    store_theme = _FakeStoreTheme({"hero": {}, "product_grid": {}, "footer": {}})
    service, _ = _service(store_theme)

    report = await service.apply(_store(), preset, apply_theme=True)

    assert report["theme"] == "draft_updated"
    order = store_theme.draft_customization_v3["templates"]["home"]["order"]
    assert order == ["hero-1", "product_grid-2"]
    # The live payload must be untouched — a preset never republishes a storefront.
    assert store_theme.customization_v3 == {}


@pytest.mark.asyncio
async def test_theme_skipped_without_active_theme():
    preset = get_preset("fashion")
    service, _ = _service(store_theme=None)
    report = await service.apply(_store(), preset, apply_theme=True)
    assert report["theme"] == "skipped_no_active_theme"


def test_available_section_types_accepts_both_manifest_shapes():
    assert _available_section_types({"hero": {}, "footer": {}}) == {"hero", "footer"}
    assert _available_section_types([{"type": "hero"}, {"nope": 1}]) == {"hero"}
    assert _available_section_types(None) == set()


def test_unimplemented_capability_never_resolves_true():
    store = _store()
    store.settings = {"capabilities": {"donations": True}}
    assert CapabilityService.resolve(store, "enterprise", "donations") is False


def test_plan_floor_gates_capability():
    store = _store()
    store.settings = {"capabilities": {"multi_warehouse": True}}
    assert CapabilityService.resolve(store, "starter", "multi_warehouse") is False
    assert CapabilityService.resolve(store, "pro", "multi_warehouse") is True


def test_store_override_beats_default():
    store = _store()
    assert CapabilityService.resolve(store, "starter", "shipping") is True
    store.settings = {"capabilities": {"shipping": False}}
    assert CapabilityService.resolve(store, "starter", "shipping") is False


def test_every_preset_capability_is_a_known_capability():
    from src.application.services.capability_service import CAPABILITIES

    for preset in SECTOR_PRESETS.values():
        for key in preset.capabilities:
            assert key in CAPABILITIES, f"{preset.key} names unknown capability {key}"

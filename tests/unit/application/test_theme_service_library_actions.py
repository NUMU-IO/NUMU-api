"""Unit tests for the Shopify-style theme library actions on ThemeService.

Covers rename / duplicate / export, which back the Online Store theme
library's per-installation menu. Uses an in-memory fake StoreTheme repo so
the business rules are exercised without a database — the DB wiring itself
is covered by the repository's own mapping (name column round-trip).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.application.services.theme_service import ThemeService
from src.core.entities.theme import StoreTheme, ThemeType


class _FakeStoreThemeRepo:
    """Minimal in-memory stand-in for StoreThemeRepository.

    Implements only the methods ThemeService's library actions touch.
    """

    def __init__(self, rows: list[StoreTheme]) -> None:
        self._rows = {r.id: r for r in rows}

    async def get_installation(
        self, store_id: UUID, installation_id: UUID
    ) -> StoreTheme | None:
        row = self._rows.get(installation_id)
        if row is None or row.store_id != store_id:
            return None
        return row

    async def update(self, entity: StoreTheme) -> StoreTheme:
        self._rows[entity.id] = entity
        return entity

    async def create(self, entity: StoreTheme) -> StoreTheme:
        self._rows[entity.id] = entity
        return entity


def _make_installation(**overrides) -> StoreTheme:
    base = {
        "id": uuid4(),
        "store_id": uuid4(),
        "tenant_id": uuid4(),
        "theme_id": uuid4(),
        "theme_version_id": uuid4(),
        "is_active": True,
        "theme_slug": "bazar",
        "theme_name": "Bazar",
        "theme_type": ThemeType.INTERNAL,
        "theme_version": "1.0.0",
        "customization": {"theme": {"primary_color": "#fff"}},
        "customization_v3": {"templates": {"index": {}}},
        "draft_customization": {"draft": True},
        "draft_customization_v3": {"draft_v3": True},
    }
    base.update(overrides)
    return StoreTheme(**base)


def _svc(repo: _FakeStoreThemeRepo) -> ThemeService:
    # theme/version repos are unused by the library actions.
    return ThemeService(theme_repo=None, version_repo=None, store_theme_repo=repo)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rename_sets_label() -> None:
    inst = _make_installation()
    svc = _svc(_FakeStoreThemeRepo([inst]))

    updated = await svc.rename_installation(inst.store_id, inst.id, "  Holiday draft  ")

    assert updated.name == "Holiday draft"  # trimmed
    assert updated.display_name == "Holiday draft"


@pytest.mark.asyncio
async def test_rename_rejects_blank() -> None:
    inst = _make_installation()
    svc = _svc(_FakeStoreThemeRepo([inst]))

    with pytest.raises(HTTPException) as exc:
        await svc.rename_installation(inst.store_id, inst.id, "   ")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_copies_payload_inactive_with_default_name() -> None:
    inst = _make_installation()
    svc = _svc(_FakeStoreThemeRepo([inst]))

    clone = await svc.duplicate_installation(inst.store_id, inst.id)

    assert clone.id != inst.id
    assert clone.is_active is False  # never clobbers the live theme
    assert clone.name == "Copy of Bazar"
    assert clone.theme_id == inst.theme_id
    assert clone.theme_version_id == inst.theme_version_id
    # Full customization payloads copied (and are independent dicts)
    assert clone.customization == inst.customization
    assert clone.customization is not inst.customization
    assert clone.customization_v3 == inst.customization_v3
    assert clone.draft_customization == inst.draft_customization


@pytest.mark.asyncio
async def test_duplicate_honors_explicit_name() -> None:
    inst = _make_installation(name="Live theme")
    svc = _svc(_FakeStoreThemeRepo([inst]))

    clone = await svc.duplicate_installation(
        inst.store_id, inst.id, name="Black Friday"
    )

    assert clone.name == "Black Friday"


@pytest.mark.asyncio
async def test_export_shape() -> None:
    inst = _make_installation(name="My Bazar")
    svc = _svc(_FakeStoreThemeRepo([inst]))

    export = await svc.export_installation(inst.store_id, inst.id)

    assert export["format"] == "numu.theme-export"
    assert export["version"] == 1
    assert export["theme"]["slug"] == "bazar"
    assert export["theme"]["name"] == "My Bazar"  # display_name wins
    assert export["customization"] == inst.customization
    assert export["customization_v3"] == inst.customization_v3
    # No store/tenant leakage in the portable doc
    assert "store_id" not in export
    assert "tenant_id" not in export


@pytest.mark.asyncio
async def test_missing_installation_raises_404() -> None:
    inst = _make_installation()
    svc = _svc(_FakeStoreThemeRepo([inst]))

    with pytest.raises(HTTPException) as exc:
        await svc.export_installation(inst.store_id, uuid4())
    assert exc.value.status_code == 404

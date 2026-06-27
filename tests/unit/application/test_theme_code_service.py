"""Unit tests for ThemeCodeService (Online Store code editor).

Covers path-traversal safety, scaffold seeding/guarding, and file CRUD via an
in-memory fake repo (no DB). The scaffold *content* correctness (placeholders
filled, valid theme.json) is also asserted against the bundled template.
"""

from __future__ import annotations

import json
import re
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.application.services.theme_code_service import ThemeCodeService
from src.core.entities.theme import StoreThemeFile


class _FakeFileRepo:
    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, str], StoreThemeFile] = {}

    async def list_for_store(self, store_id: UUID) -> list[StoreThemeFile]:
        return [f for (s, _), f in self._rows.items() if s == store_id]

    async def get(self, store_id: UUID, path: str) -> StoreThemeFile | None:
        return self._rows.get((store_id, path))

    async def count_for_store(self, store_id: UUID) -> int:
        return len([1 for (s, _) in self._rows if s == store_id])

    async def upsert(self, *, store_id, tenant_id, path, content) -> StoreThemeFile:
        f = StoreThemeFile(
            id=uuid4(),
            store_id=store_id,
            tenant_id=tenant_id,
            path=path,
            content=content,
        )
        self._rows[(store_id, path)] = f
        return f

    async def bulk_upsert(self, *, store_id, tenant_id, files) -> int:
        for path, content in files.items():
            await self.upsert(
                store_id=store_id, tenant_id=tenant_id, path=path, content=content
            )
        return len(files)

    async def delete(self, store_id: UUID, path: str) -> bool:
        return self._rows.pop((store_id, path), None) is not None

    async def delete_all_for_store(self, store_id: UUID) -> int:
        keys = [(s, p) for (s, p) in self._rows if s == store_id]
        for k in keys:
            self._rows.pop(k)
        return len(keys)


def _svc() -> tuple[ThemeCodeService, _FakeFileRepo]:
    repo = _FakeFileRepo()
    return ThemeCodeService(file_repo=repo), repo


@pytest.mark.parametrize(
    "bad",
    ["../etc/passwd", "a/../../b", "..", "", "   ", "foo/../../bar", "a/../b"],
)
def test_safe_path_rejects_traversal(bad: str) -> None:
    with pytest.raises(HTTPException) as exc:
        ThemeCodeService._safe_path(bad)
    assert exc.value.status_code == 422


def test_safe_path_normalizes_backslashes_and_leading_slash() -> None:
    assert (
        ThemeCodeService._safe_path("src\\sections\\Hero.tsx")
        == "src/sections/Hero.tsx"
    )
    assert ThemeCodeService._safe_path("/theme.json") == "theme.json"


def test_render_scaffold_fills_all_placeholders() -> None:
    files = ThemeCodeService.render_scaffold(
        theme_id="acme", theme_name="Acme", author="ACME", version="2.1.0"
    )
    assert {"theme.json", "settings_schema.json", "styles.css"} <= set(files)
    leftover = [p for p, c in files.items() if re.search(r"__[A-Z_]+__", c)]
    assert not leftover, leftover
    assert json.loads(files["theme.json"])["id"] == "acme"


@pytest.mark.asyncio
async def test_scaffold_seeds_then_guards_against_clobber() -> None:
    svc, _ = _svc()
    store_id, tenant_id = uuid4(), uuid4()

    count = await svc.scaffold(
        store_id=store_id, tenant_id=tenant_id, theme_name="Shop"
    )
    assert count == 60

    # Second scaffold without overwrite must 409 (protect merchant edits)
    with pytest.raises(HTTPException) as exc:
        await svc.scaffold(store_id=store_id, tenant_id=tenant_id, theme_name="Shop")
    assert exc.value.status_code == 409

    # overwrite=True wipes + reseeds
    again = await svc.scaffold(
        store_id=store_id, tenant_id=tenant_id, theme_name="Shop", overwrite=True
    )
    assert again == 60


@pytest.mark.asyncio
async def test_write_read_delete_roundtrip() -> None:
    svc, _ = _svc()
    store_id, tenant_id = uuid4(), uuid4()

    await svc.write_file(
        store_id=store_id,
        tenant_id=tenant_id,
        path="src/x.ts",
        content="export const a=1",
    )
    f = await svc.read_file(store_id, "src/x.ts")
    assert f.content == "export const a=1"

    await svc.delete_file(store_id, "src/x.ts")
    with pytest.raises(HTTPException) as exc:
        await svc.read_file(store_id, "src/x.ts")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_write_file_rejects_oversize() -> None:
    svc, _ = _svc()
    with pytest.raises(HTTPException) as exc:
        await svc.write_file(
            store_id=uuid4(),
            tenant_id=uuid4(),
            path="big.txt",
            content="x" * (512 * 1024 + 1),
        )
    assert exc.value.status_code == 413

"""Unit tests for the code-editor build task wiring (build_theme_from_files).

These test the GLUE I added — materialize → shared core (with the right
activation args) → Redis status — not the heavy build internals (Docker/npm/
R2), which are shared with the already-exercised ZIP path. Externals are
patched so no real build/upload/DB happens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.infrastructure.messaging.tasks.theme_build_tasks as build_mod
import src.infrastructure.messaging.tasks.theme_upload_tasks as up


@pytest.fixture
def patched(monkeypatch):
    """Patch materialize + the shared core + redis status; capture calls."""
    calls: dict = {"materialize": None, "core": None, "status": []}

    def fake_materialize(store_id: str, dest: Path) -> None:
        calls["materialize"] = (store_id, dest)
        # Pretend we wrote at least one file so the dir exists.
        dest.mkdir(parents=True, exist_ok=True)

    def fake_core(**kwargs):
        calls["core"] = kwargs
        return {"build_id": kwargs["build_id"], "status": "complete", "theme_id": "t1"}

    def fake_status(build_id: str, **kw):
        calls["status"].append({"build_id": build_id, **kw})

    monkeypatch.setattr(build_mod, "_materialize_store_files", fake_materialize)
    monkeypatch.setattr(up, "_build_register_activate", fake_core)
    monkeypatch.setattr(up, "_redis_status", fake_status)
    return calls


def test_build_from_files_delegates_with_activation(patched):
    result = up.build_theme_from_files.apply(
        kwargs={"store_id": "store-1", "build_id": "b1", "tenant_id": "ten-1"}
    ).get()

    assert result["status"] == "complete"
    # materialized for the right store
    assert patched["materialize"][0] == "store-1"
    # shared core invoked with activation wired to the store + Redis status
    core = patched["core"]
    assert core["activate_for_store_id"] == "store-1"
    assert core["tenant_id"] == "ten-1"
    assert core["build_id"] == "b1"
    assert core["status"] is up._redis_status


def test_build_from_files_marks_failed_on_build_error(patched, monkeypatch):
    def boom(**kwargs):
        raise up.ThemeBuildError("npm install failed")

    monkeypatch.setattr(up, "_build_register_activate", boom)

    result = up.build_theme_from_files.apply(
        kwargs={"store_id": "store-1", "build_id": "b2", "tenant_id": "ten-1"}
    ).get()

    assert result["status"] == "failed"
    assert "npm install failed" in result["error"]
    # the failure was reported through the Redis status callback
    assert any(s.get("status") == "failed" for s in patched["status"])


def test_redis_status_writes_to_build_store(monkeypatch):
    """_redis_status forwards updates to the Redis-backed build store."""
    captured: dict = {}

    class _FakeStore:
        async def update(self, build_id, data):
            captured["build_id"] = build_id
            captured["data"] = data

    monkeypatch.setattr(
        up, "get_theme_build_store", lambda: _FakeStore(), raising=False
    )
    # get_theme_build_store is imported inside _redis_status; patch at source too
    import src.infrastructure.cache.theme_build_store as tbs

    monkeypatch.setattr(tbs, "get_theme_build_store", lambda: _FakeStore())

    up._redis_status("b9", status="building", theme_slug="x")

    assert captured["build_id"] == "b9"
    assert captured["data"]["status"] == "building"
    assert captured["data"]["theme_slug"] == "x"

"""Tests for the theme update detection service (Phase 5.1).

build_notification is pure (classify + construct); check_store orchestrates
repo lookups. Both are exercised with light fakes — no DB.
"""

import uuid
from types import SimpleNamespace

import pytest

from src.application.services.theme_update_service import (
    ThemeUpdateService,
    build_notification,
)


def _v(version_string, settings=None, sections=None, release_notes="", vid=None):
    return SimpleNamespace(
        id=vid or uuid.uuid4(),
        version_string=version_string,
        settings_schema=settings if settings is not None else [],
        section_schemas=sections if sections is not None else {},
        release_notes=release_notes,
    )


class TestBuildNotification:
    def test_none_latest_returns_none(self):
        assert (
            build_notification(
                store_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                marketplace_theme_id=uuid.uuid4(),
                installed_version=_v("0.1.0"),
                latest_version=None,
            )
            is None
        )

    def test_same_version_returns_none(self):
        v = _v("0.1.0")
        assert (
            build_notification(
                store_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                marketplace_theme_id=uuid.uuid4(),
                installed_version=v,
                latest_version=v,
            )
            is None
        )

    def test_newer_breaking_is_manual(self):
        installed = _v("0.1.0", settings=[{"id": "a", "type": "text"}])
        latest = _v("0.2.0", settings=[])  # removed setting → breaking
        n = build_notification(
            store_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            marketplace_theme_id=uuid.uuid4(),
            installed_version=installed,
            latest_version=latest,
        )
        assert n is not None
        assert n.classification == "manual"
        assert n.from_version == "0.1.0"
        assert n.to_version == "0.2.0"
        assert n.to_version_id == latest.id
        assert n.from_version_id == installed.id
        assert n.status == "pending"
        assert any(c["kind"] == "setting_removed" for c in n.changes)

    def test_newer_additive_is_automatic(self):
        installed = _v("0.1.0", settings=[{"id": "a", "type": "text"}])
        latest = _v(
            "0.2.0",
            settings=[{"id": "a", "type": "text"}, {"id": "b", "type": "text"}],
            release_notes="Added setting b",
        )
        n = build_notification(
            store_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            marketplace_theme_id=uuid.uuid4(),
            installed_version=installed,
            latest_version=latest,
        )
        assert n.classification == "automatic"
        assert n.release_notes == "Added setting b"


class _FakeMarketplaceRepo:
    def __init__(self, installs, versions, latest_by_theme):
        self._installs = installs
        self._versions = {v.id: v for v in versions}
        self._latest = latest_by_theme

    async def list_installations(self, store_id):
        return self._installs

    async def get_version_by_id(self, version_id):
        return self._versions.get(version_id)

    async def get_latest_published_version(self, theme_id):
        return self._latest.get(theme_id)


class _FakeNotifRepo:
    def __init__(self):
        self.rows = {}

    async def get_for_version(self, store_id, to_version_id):
        for n in self.rows.values():
            if n.store_id == store_id and n.to_version_id == to_version_id:
                return n
        return None

    async def create(self, n):
        self.rows[n.id] = n
        return n

    async def update(self, n):
        self.rows[n.id] = n
        return n


def _store():
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())


class TestCheckStore:
    @pytest.mark.asyncio
    async def test_creates_notification_for_newer_version(self):
        store = _store()
        theme = uuid.uuid4()
        installed = _v("0.1.0", settings=[{"id": "a", "type": "text"}])
        latest = _v("0.2.0", settings=[])  # breaking
        inst = SimpleNamespace(
            marketplace_theme_id=theme,
            marketplace_version_id=installed.id,
            is_active=True,
        )
        svc = ThemeUpdateService(
            _FakeMarketplaceRepo([inst], [installed, latest], {theme: latest}),
            _FakeNotifRepo(),
        )
        created = await svc.check_store(store)
        assert len(created) == 1
        assert created[0].classification == "manual"

    @pytest.mark.asyncio
    async def test_idempotent_does_not_duplicate(self):
        store = _store()
        theme = uuid.uuid4()
        installed = _v("0.1.0", settings=[{"id": "a", "type": "text"}])
        latest = _v("0.2.0", settings=[])
        inst = SimpleNamespace(
            marketplace_theme_id=theme,
            marketplace_version_id=installed.id,
            is_active=True,
        )
        repo = _FakeNotifRepo()
        svc = ThemeUpdateService(
            _FakeMarketplaceRepo([inst], [installed, latest], {theme: latest}), repo
        )
        await svc.check_store(store)
        await svc.check_store(store)  # second scan
        assert len(repo.rows) == 1  # refreshed, not duplicated

    @pytest.mark.asyncio
    async def test_skips_when_already_on_latest(self):
        store = _store()
        theme = uuid.uuid4()
        latest = _v("0.2.0")
        inst = SimpleNamespace(
            marketplace_theme_id=theme,
            marketplace_version_id=latest.id,  # already latest
            is_active=True,
        )
        repo = _FakeNotifRepo()
        svc = ThemeUpdateService(
            _FakeMarketplaceRepo([inst], [latest], {theme: latest}), repo
        )
        assert await svc.check_store(store) == []
        assert len(repo.rows) == 0

    @pytest.mark.asyncio
    async def test_skips_inactive_install(self):
        store = _store()
        theme = uuid.uuid4()
        installed = _v("0.1.0")
        latest = _v("0.2.0")
        inst = SimpleNamespace(
            marketplace_theme_id=theme,
            marketplace_version_id=installed.id,
            is_active=False,  # not the active theme
        )
        repo = _FakeNotifRepo()
        svc = ThemeUpdateService(
            _FakeMarketplaceRepo([inst], [installed, latest], {theme: latest}), repo
        )
        assert await svc.check_store(store) == []
        assert len(repo.rows) == 0

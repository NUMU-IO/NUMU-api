"""Autosave optimistic-concurrency (etag / 409) tests — Phase 1.4.

Autosave clobber was a silent-reliability hole: two editor tabs could
overwrite each other. ``autosave_draft`` now takes an ``expected_etag`` and
raises ``StaleEtagError`` (→ route 409) when another save landed since the
draft was loaded, returning the live etag + draft so the client can rebase.

These exercise the conflict-detection logic directly with tiny fake repos
(no DB) — the etag check runs before any write, so a fake ``store_theme`` is
all that's needed. The HTTP 409 mapping itself is covered by the Phase 5.5
negative pass.
"""

import uuid
from datetime import UTC, datetime

import pytest

from src.application.services.theme_v3_service import (
    StaleEtagError,
    ThemeV3Service,
    _etag_from,
)

VALID_V3 = {
    "schema_version": 3,
    "theme_id": "bazar",
    "global_settings": {},
    "templates": {},
    "section_groups": {},
}


class _FakeStoreTheme:
    def __init__(self, updated_at, draft_v3=None):
        self.updated_at = updated_at
        self.draft_customization_v3 = draft_v3
        self.customization_v3 = None
        self.customization = None
        self.draft_customization = None
        self.section_schemas = None
        self.presets = None


class _FakeStoreThemeRepo:
    def __init__(self, store_theme):
        self._st = store_theme
        self.updated = []

    async def get_active_for_store(self, store_id):
        return self._st

    async def update(self, st):
        self.updated.append(st)
        return st


class _FakeVersionRepo:
    def __init__(self):
        self.created = []

    async def create(self, version):
        self.created.append(version)
        return version

    async def prune_autosaves(self, store_id, keep):
        return None


class TestEtagFrom:
    def test_none_returns_none(self):
        assert _etag_from(None) is None

    def test_datetime_encodes_to_isoformat_and_is_stable(self):
        dt = datetime(2026, 6, 1, 12, 0, 0, 123456, tzinfo=UTC)
        assert _etag_from(dt) == dt.isoformat()
        assert _etag_from(dt) == _etag_from(dt)

    def test_distinct_timestamps_yield_distinct_etags(self):
        a = datetime(2026, 6, 1, 12, 0, 0, 1, tzinfo=UTC)
        b = datetime(2026, 6, 1, 12, 0, 0, 2, tzinfo=UTC)
        assert _etag_from(a) != _etag_from(b)

    def test_str_passthrough(self):
        assert _etag_from("v123") == "v123"


class TestAutosaveEtagConflict:
    @pytest.mark.asyncio
    async def test_stale_etag_raises_with_live_etag_and_draft(self):
        dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        st = _FakeStoreTheme(
            dt, draft_v3={**VALID_V3, "global_settings": {"primary_color": "#abc"}}
        )
        svc = ThemeV3Service(_FakeStoreThemeRepo(st), _FakeVersionRepo())
        with pytest.raises(StaleEtagError) as ei:
            await svc.autosave_draft(uuid.uuid4(), VALID_V3, expected_etag="stale")
        # The 409 body carries the live etag + current draft so the client
        # can rebase its edits instead of blindly clobbering.
        assert ei.value.current_etag == dt.isoformat()
        assert ei.value.current_draft["schema_version"] == 3
        assert ei.value.current_draft["global_settings"]["primary_color"] == "#abc"

    @pytest.mark.asyncio
    async def test_matching_etag_saves_and_versions(self):
        dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        st = _FakeStoreTheme(dt, draft_v3=None)
        repo = _FakeStoreThemeRepo(st)
        vrepo = _FakeVersionRepo()
        svc = ThemeV3Service(repo, vrepo)
        out = await svc.autosave_draft(
            uuid.uuid4(), VALID_V3, expected_etag=dt.isoformat()
        )
        assert out["schema_version"] == 3
        # Dual-write happened (V3 column set) + an autosave version was created.
        assert st.draft_customization_v3["theme_id"] == "bazar"
        assert len(repo.updated) == 1
        assert len(vrepo.created) == 1

    @pytest.mark.asyncio
    async def test_no_expected_etag_skips_conflict_check(self):
        # First-write callers omit expected_etag → no 409 even though the
        # stored etag differs (bootstrap / existing fixtures).
        dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        st = _FakeStoreTheme(dt, draft_v3=None)
        svc = ThemeV3Service(_FakeStoreThemeRepo(st), _FakeVersionRepo())
        out = await svc.autosave_draft(uuid.uuid4(), VALID_V3)
        assert out["schema_version"] == 3

    @pytest.mark.asyncio
    async def test_no_active_theme_raises_valueerror(self):
        svc = ThemeV3Service(_FakeStoreThemeRepo(None), _FakeVersionRepo())
        with pytest.raises(ValueError, match="No active theme"):
            await svc.autosave_draft(uuid.uuid4(), VALID_V3, expected_etag="x")

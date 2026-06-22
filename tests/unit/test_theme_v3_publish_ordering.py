"""V3 publish must COMMIT before any cache invalidation / storefront revalidate.

Root cause of the residual publish-staleness: the request session commits only
in ``get_db_session``'s dependency finalizer — AFTER the route handler returns —
yet cache invalidation + Next.js revalidation were fired inside the handler,
i.e. while the publish write was still uncommitted. A racing storefront read
(the live-preview iframe, the merchant's own reload, a bot) then re-cached the
stale pre-commit row, and ``revalidateTag(tag, {expire:0})`` only expires once,
so the stale entry was served fresh until the 60s ISR safety-net elapsed.

These tests pin the new contract on ``ThemeV3Service.publish``:
  * it PERSISTS then COMMITS the transaction before returning;
  * it freshness-verifies via a separate session and returns a revision id +
    content hash + ``verified`` flag;
  * it does NOT itself revalidate the storefront — that's the route's job,
    strictly after the commit.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.services.theme_v3_service import ThemeV3Service

VALID_DRAFT = {
    "schema_version": 3,
    "theme_id": "bazar",
    "global_settings": {"primary_color": "#000"},
}


class FakeSession:
    """Records commits + executed statements (commit_and_restore_rls runs here)."""

    def __init__(self) -> None:
        self.commit_count = 0
        self.executed: list = []

    async def commit(self) -> None:
        self.commit_count += 1

    async def execute(self, *args, **kwargs):
        self.executed.append((args, kwargs))


class FakeStoreTheme:
    def __init__(self, draft: dict) -> None:
        self.draft_customization_v3 = dict(draft)
        self.customization_v3: dict = {}
        self.draft_customization: dict = {}
        self.customization: dict = {}


class FakeStoreThemeRepo:
    def __init__(self, store_theme: FakeStoreTheme, session: FakeSession) -> None:
        self._st = store_theme
        self.session = session
        self.update_calls = 0

    async def get_active_for_store(self, store_id):
        return self._st

    async def update(self, store_theme):
        self.update_calls += 1
        return store_theme


class FakeVersionRepo:
    def __init__(self) -> None:
        self.created: list = []

    async def create(self, version):
        self.created.append(version)
        return version


@pytest.fixture
def svc_setup(monkeypatch):
    session = FakeSession()
    st = FakeStoreTheme(VALID_DRAFT)
    repo = FakeStoreThemeRepo(st, session)
    vrepo = FakeVersionRepo()
    svc = ThemeV3Service(store_theme_repo=repo, version_repo=vrepo)

    # Skip the real separate-session DB read-back (no DB in a unit test) but
    # record that it ran and with which hash.
    verify_calls: list = []

    async def fake_verify(self, store_id, expected_hash):  # noqa: ANN001
        verify_calls.append((store_id, expected_hash))
        return True

    monkeypatch.setattr(ThemeV3Service, "_verify_published", fake_verify)
    return svc, session, st, repo, vrepo, verify_calls


@pytest.mark.asyncio
async def test_publish_commits_inside_the_call(svc_setup):
    svc, session, st, _repo, vrepo, verify_calls = svc_setup

    result = await svc.publish(store_id=uuid4())

    # The transaction is committed by publish itself — NOT deferred to the
    # dependency finalizer that runs after the handler (and after revalidation).
    assert session.commit_count == 1
    # Draft was promoted to published.
    assert st.customization_v3 == result["published"]
    assert st.draft_customization_v3 == {}
    # A published version row was created.
    assert len(vrepo.created) == 1 and vrepo.created[0].is_published is True
    # Returns a revision fingerprint + verified flag.
    assert result["revision_id"]
    assert result["content_hash"]
    assert result["verified"] is True
    # Freshness read-back ran against the published hash.
    assert verify_calls and verify_calls[0][1] == result["content_hash"]


@pytest.mark.asyncio
async def test_publish_does_not_revalidate_from_the_service(svc_setup, monkeypatch):
    svc, *_ = svc_setup

    import src.infrastructure.external_services.nextjs_revalidation as rv

    called: list = []

    async def boom(*a, **k):
        called.append((a, k))

    monkeypatch.setattr(rv, "revalidate_on_customization_publish", boom)
    monkeypatch.setattr(rv, "revalidate_on_customization_publish_traced", boom)

    await svc.publish(store_id=uuid4())

    # Revalidation is the ROUTE's responsibility (after commit). The service
    # must never trigger it (that's what produced the pre-commit race).
    assert called == []


@pytest.mark.asyncio
async def test_publish_raises_without_a_v3_draft(svc_setup):
    svc, session, st, *_ = svc_setup
    st.draft_customization_v3 = {}  # nothing to publish

    with pytest.raises(ValueError):
        await svc.publish(store_id=uuid4())
    # No commit happened on the failed publish.
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_content_hash_is_stable_and_payload_sensitive(svc_setup):
    svc, _session, st, *_ = svc_setup

    r1 = await svc.publish(store_id=uuid4())

    st.draft_customization_v3 = dict(VALID_DRAFT)
    r2 = await svc.publish(store_id=uuid4())
    assert r1["content_hash"] == r2["content_hash"]

    st.draft_customization_v3 = {
        **VALID_DRAFT,
        "global_settings": {"primary_color": "#fff"},
    }
    r3 = await svc.publish(store_id=uuid4())
    assert r3["content_hash"] != r1["content_hash"]

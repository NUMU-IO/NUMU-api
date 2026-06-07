"""Page repository tests (Phase 4.4b — merchant content pages).

Covers CRUD, bilingual title/body + SEO round-trip, the published-only filter
the PUBLIC storefront resolver relies on (drafts must never leak), store-level
isolation, and the application-level tenant guard.

Note on RLS / SQLite: see test_menu_repository.py's module docstring — the
execute-based tests run with NO tenant context, and the tenant guard is
asserted via the compiled WHERE clause. DB-enforced RLS + the HTTP public
resolver are covered by the Phase 5.5 Postgres pass.
"""

import uuid

import pytest
from sqlalchemy import select

from src.core.entities.page import Page
from src.infrastructure.database.connection import reset_tenant_id, set_tenant_id
from src.infrastructure.database.models.tenant.page import PageModel
from src.infrastructure.repositories.page_repository import PageRepository


@pytest.fixture(autouse=True)
def _reset_tenant():
    yield
    reset_tenant_id()


class TestPageRepository:
    @pytest.mark.asyncio
    async def test_create_and_get_by_handle_bilingual(self, test_session):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = PageRepository(test_session)
        await repo.create(
            Page(
                store_id=store_id,
                tenant_id=tid,
                handle="about",
                title={"en": "About", "ar": "من نحن"},
                body={"en": "<p>Hello</p>", "ar": "<p>مرحبا</p>"},
                seo={"description": {"en": "About us", "ar": "معلومات عنا"}},
            )
        )
        got = await repo.get_by_handle(store_id, "about")
        assert got is not None
        assert got.title["ar"] == "من نحن"
        assert got.body["en"] == "<p>Hello</p>"
        assert got.seo["description"]["ar"] == "معلومات عنا"
        assert got.template == "page"  # default
        assert got.content_v3 == {}  # reserved, empty until per-page editor

    @pytest.mark.asyncio
    async def test_published_filter_for_public_resolver(self, test_session):
        # get_by_store(include_unpublished=False) is what the PUBLIC resolver
        # uses — unpublished drafts must not reach the storefront.
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = PageRepository(test_session)
        await repo.create(
            Page(store_id=store_id, tenant_id=tid, handle="about", is_published=True)
        )
        await repo.create(
            Page(
                store_id=store_id,
                tenant_id=tid,
                handle="draft-page",
                is_published=False,
            )
        )
        public = await repo.get_by_store(store_id, include_unpublished=False)
        assert [p.handle for p in public] == ["about"]
        allp = await repo.get_by_store(store_id, include_unpublished=True)
        assert {p.handle for p in allp} == {"about", "draft-page"}

    @pytest.mark.asyncio
    async def test_update_and_delete(self, test_session):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = PageRepository(test_session)
        p = await repo.create(
            Page(store_id=store_id, tenant_id=tid, handle="returns", is_published=False)
        )
        p.is_published = True
        p.body = {"en": "<p>30-day returns</p>", "ar": "<p>إرجاع</p>"}
        updated = await repo.update(p)
        assert updated.is_published is True
        assert updated.body["en"] == "<p>30-day returns</p>"
        assert await repo.delete(p.id) is True
        assert await repo.get_by_handle(store_id, "returns") is None

    @pytest.mark.asyncio
    async def test_store_isolation(self, test_session):
        tid, store_a, store_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        repo = PageRepository(test_session)
        await repo.create(Page(store_id=store_a, tenant_id=tid, handle="about"))
        await repo.create(Page(store_id=store_b, tenant_id=tid, handle="about"))
        a_pages = await repo.get_by_store(store_a)
        assert len(a_pages) == 1
        assert a_pages[0].store_id == store_a

    def test_tenant_filter_engages_only_with_context(self, test_session):
        repo = PageRepository(test_session)
        reset_tenant_id()
        base = repo._tenant_filter(select(PageModel))
        assert base.whereclause is None

        set_tenant_id(uuid.uuid4())
        scoped = repo._tenant_filter(select(PageModel))
        assert scoped.whereclause is not None
        assert "tenant_id" in str(scoped.whereclause)

"""Menu repository + seed tests (Phase 2.1).

Covers the store-level navigation model: bilingual default seed, CRUD, the
ordered/active-filtered ``get_by_store`` the storefront resolver uses,
store-level isolation, and the application-level tenant guard.

Note on RLS: true PostgreSQL row-level security can't be exercised under the
SQLite test engine. Worse, ``get_tenant_id()`` returns a *string*, and
comparing a Postgres ``UUID`` column to a string only works under the asyncpg
driver — under SQLite it raises in the type's bind processor. So the
execute-based tests run with NO tenant context (the filter is then a no-op),
and the tenant guard itself is asserted by inspecting the compiled WHERE
clause (no execution). DB-enforced RLS is covered by the Phase 5.5 Postgres
pass.
"""

import uuid

import pytest
from sqlalchemy import select

from src.core.entities.menu import Menu, build_default_menus
from src.infrastructure.database.connection import reset_tenant_id, set_tenant_id
from src.infrastructure.database.models.tenant.menu import MenuModel
from src.infrastructure.repositories.menu_repository import MenuRepository


@pytest.fixture(autouse=True)
def _reset_tenant():
    # Tenant context is a ContextVar — clear it after each test so it can't
    # leak into the next (a leaked context would crash later SELECTs).
    yield
    reset_tenant_id()


class TestBuildDefaultMenus:
    def test_seeds_main_menu_and_footer_bilingual(self):
        store_id = uuid.uuid4()
        tid = uuid.uuid4()
        menus = build_default_menus(store_id, tid)
        assert {m.handle for m in menus} == {"main-menu", "footer"}
        main = next(m for m in menus if m.handle == "main-menu")
        assert main.title["en"] == "Main menu"
        assert main.title["ar"]  # Egyptian Arabic present
        assert all(i["label"]["en"] and i["label"]["ar"] for i in main.items)
        for m in menus:
            assert m.store_id == store_id
            assert m.tenant_id == tid
            assert m.is_active is True


class TestMenuRepository:
    @pytest.mark.asyncio
    async def test_create_and_get_by_handle(self, test_session):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = MenuRepository(test_session)
        created = await repo.create(
            Menu(
                store_id=store_id,
                tenant_id=tid,
                handle="main-menu",
                title={"en": "Main", "ar": "الرئيسية"},
                items=[
                    {"id": "1", "label": {"en": "Home", "ar": "الرئيسية"}, "url": "/"}
                ],
            )
        )
        assert created.id is not None
        got = await repo.get_by_handle(store_id, "main-menu")
        assert got is not None
        assert got.title["ar"] == "الرئيسية"
        assert got.items[0]["url"] == "/"

    @pytest.mark.asyncio
    async def test_get_by_store_orders_by_handle_and_filters_inactive(
        self, test_session
    ):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = MenuRepository(test_session)
        await repo.create(Menu(store_id=store_id, tenant_id=tid, handle="footer"))
        await repo.create(Menu(store_id=store_id, tenant_id=tid, handle="main-menu"))
        await repo.create(
            Menu(store_id=store_id, tenant_id=tid, handle="hidden", is_active=False)
        )
        active = await repo.get_by_store(store_id)
        assert [m.handle for m in active] == ["footer", "main-menu"]
        allm = await repo.get_by_store(store_id, include_inactive=True)
        assert {m.handle for m in allm} == {"footer", "main-menu", "hidden"}

    @pytest.mark.asyncio
    async def test_update_and_delete(self, test_session):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = MenuRepository(test_session)
        m = await repo.create(
            Menu(store_id=store_id, tenant_id=tid, handle="footer", items=[])
        )
        m.items = [{"id": "x", "label": {"en": "FAQ", "ar": "الأسئلة"}, "url": "/faq"}]
        updated = await repo.update(m)
        assert updated.items[0]["url"] == "/faq"
        assert await repo.delete(m.id) is True
        assert await repo.get_by_handle(store_id, "footer") is None

    @pytest.mark.asyncio
    async def test_store_isolation(self, test_session):
        # Two stores under one tenant — get_by_store returns only its own.
        tid, store_a, store_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        repo = MenuRepository(test_session)
        await repo.create(Menu(store_id=store_a, tenant_id=tid, handle="main-menu"))
        await repo.create(Menu(store_id=store_b, tenant_id=tid, handle="main-menu"))
        a_menus = await repo.get_by_store(store_a)
        assert len(a_menus) == 1
        assert a_menus[0].store_id == store_a

    def test_tenant_filter_engages_only_with_context(self, test_session):
        # The app-level half of RLS: _tenant_filter appends a tenant_id WHERE
        # clause iff a tenant context is set. (Executing it under SQLite would
        # crash on the str↔UUID compare — see the module docstring — so we
        # assert the compiled clause instead.)
        repo = MenuRepository(test_session)
        reset_tenant_id()
        base = repo._tenant_filter(select(MenuModel))
        assert base.whereclause is None

        set_tenant_id(uuid.uuid4())
        scoped = repo._tenant_filter(select(MenuModel))
        assert scoped.whereclause is not None
        assert "tenant_id" in str(scoped.whereclause)

"""Repository tests for theme update notifications (Phase 5.1).

CRUD + per-version idempotency lookup + status filtering + store isolation +
the app-level tenant guard. (As with menus/pages: execute-based tests run
without a tenant context — comparing a PG UUID column to get_tenant_id()'s
string crashes under SQLite — and the tenant guard is asserted via the
compiled WHERE clause. True RLS is covered by the Phase 5.5 Postgres pass.)
"""

import uuid

import pytest
from sqlalchemy import select

from src.core.entities.theme_update_notification import ThemeUpdateNotification
from src.infrastructure.database.connection import reset_tenant_id, set_tenant_id
from src.infrastructure.database.models.tenant.theme_update_notification import (
    ThemeUpdateNotificationModel,
)
from src.infrastructure.repositories.theme_update_notification_repository import (
    ThemeUpdateNotificationRepository,
)


@pytest.fixture(autouse=True)
def _reset_tenant():
    yield
    reset_tenant_id()


def _notif(
    store_id, tenant_id, to_version_id=None, status="pending", classification="manual"
):
    return ThemeUpdateNotification(
        store_id=store_id,
        tenant_id=tenant_id,
        theme_id=uuid.uuid4(),
        from_version_id=uuid.uuid4(),
        to_version_id=to_version_id or uuid.uuid4(),
        from_version="0.1.0",
        to_version="0.2.0",
        classification=classification,
        changes=[
            {
                "kind": "setting_removed",
                "target": "global.setting:a",
                "breaking": True,
                "detail": "x",
            }
        ],
        release_notes="Notes",
        status=status,
    )


class TestThemeUpdateNotificationRepository:
    @pytest.mark.asyncio
    async def test_create_and_get_for_version(self, test_session):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = ThemeUpdateNotificationRepository(test_session)
        n = _notif(store_id, tid)
        created = await repo.create(n)
        assert created.id is not None
        found = await repo.get_for_version(store_id, n.to_version_id)
        assert found is not None
        assert found.classification == "manual"
        assert found.changes[0]["breaking"] is True

    @pytest.mark.asyncio
    async def test_get_by_store_filters_by_status(self, test_session):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = ThemeUpdateNotificationRepository(test_session)
        await repo.create(_notif(store_id, tid, status="pending"))
        await repo.create(_notif(store_id, tid, status="applied"))
        pending = await repo.get_by_store(store_id, status="pending")
        assert len(pending) == 1
        assert pending[0].status == "pending"
        all_rows = await repo.get_by_store(store_id)
        assert len(all_rows) == 2

    @pytest.mark.asyncio
    async def test_update_status_transition(self, test_session):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = ThemeUpdateNotificationRepository(test_session)
        n = await repo.create(_notif(store_id, tid))
        n.status = "applied"
        updated = await repo.update(n)
        assert updated.status == "applied"
        assert (await repo.get_by_store(store_id, status="pending")) == []

    @pytest.mark.asyncio
    async def test_delete(self, test_session):
        tid, store_id = uuid.uuid4(), uuid.uuid4()
        repo = ThemeUpdateNotificationRepository(test_session)
        n = await repo.create(_notif(store_id, tid))
        assert await repo.delete(n.id) is True
        assert await repo.get_by_id(n.id) is None

    @pytest.mark.asyncio
    async def test_store_isolation(self, test_session):
        tid, store_a, store_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        repo = ThemeUpdateNotificationRepository(test_session)
        await repo.create(_notif(store_a, tid))
        await repo.create(_notif(store_b, tid))
        assert len(await repo.get_by_store(store_a)) == 1

    def test_tenant_filter_engages_only_with_context(self, test_session):
        repo = ThemeUpdateNotificationRepository(test_session)
        reset_tenant_id()
        base = repo._tenant_filter(select(ThemeUpdateNotificationModel))
        assert base.whereclause is None

        set_tenant_id(uuid.uuid4())
        scoped = repo._tenant_filter(select(ThemeUpdateNotificationModel))
        assert scoped.whereclause is not None
        assert "tenant_id" in str(scoped.whereclause)

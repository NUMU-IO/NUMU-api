"""BIS-K-04: uninstall schedules the 30-day purge for every app that registers
a purger, Partner Apps included, and ``purge_due`` runs it; an app without one
schedules nothing; the NUMU Apps behave as before."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.v1.routes.stores import apps as store_apps
from src.application.services import app_webhooks, numu_apps
from src.application.services.numu_apps import purge_due
from src.infrastructure.messaging.tasks import app_redact_task
from tests.unit.test_numu_apps import _PurgeSession


class _UninstallSession:
    """Just enough session for the uninstall route."""

    def __init__(self, install, app):
        self.install, self.app = install, app

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _query):
        return SimpleNamespace(scalar_one_or_none=lambda: self.install)

    async def get(self, _model, _id):
        return self.app

    def add(self, _row):
        pass

    async def delete(self, _row):
        pass

    async def commit(self):
        pass


@pytest.fixture
def uninstall(monkeypatch):
    schedule = AsyncMock()
    monkeypatch.setattr(store_apps, "schedule_purge", schedule)
    monkeypatch.setattr(store_apps, "_revalidate_app_settings", AsyncMock())
    monkeypatch.setattr(app_webhooks, "deliver_app_event", AsyncMock())
    monkeypatch.setattr(
        app_redact_task.app_store_redact_task, "apply_async", MagicMock()
    )

    async def run(slug, *, partner: bool):
        app = SimpleNamespace(
            id=uuid4(), slug=slug, developer_id=uuid4() if partner else None
        )
        install = SimpleNamespace(app_id=app.id, created_at=None)
        monkeypatch.setattr(
            store_apps, "AsyncSessionLocal", lambda: _UninstallSession(install, app)
        )
        store = SimpleNamespace(id=uuid4())
        await store_apps.uninstall_app(store.id, slug, store=store)
        return schedule

    return run


@pytest.mark.asyncio
async def test_a_partner_app_with_a_purger_schedules_the_purge(uninstall, monkeypatch):
    monkeypatch.setitem(numu_apps.PURGERS, "bis-test", AsyncMock())

    schedule = await uninstall("bis-test", partner=True)

    schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_partner_app_without_a_purger_schedules_nothing(uninstall):
    schedule = await uninstall("no-data-app", partner=True)

    schedule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["inbox", "whatsapp"])
async def test_numu_apps_still_schedule_theirs(uninstall, slug):
    schedule = await uninstall(slug, partner=False)

    schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_due_runs_a_partner_apps_purger(monkeypatch):
    purger = AsyncMock()
    monkeypatch.setitem(numu_apps.PURGERS, "bis-test", purger)
    store_id = uuid4()

    stats = await purge_due(_PurgeSession([(uuid4(), store_id, "bis-test")]))

    assert stats == {"purged": 1}
    purger.assert_awaited_once()
    assert purger.await_args.args[1] == store_id


@pytest.mark.asyncio
async def test_a_dev_reinstall_cancels_the_pending_purge(monkeypatch):
    """Reinstalled inside the window: the app's data must survive."""
    from src.api.v1.routes import partner_apps

    app = SimpleNamespace(id=uuid4(), slug="bis-test", private_store_id=None)
    store = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    monkeypatch.setattr(partner_apps, "_own_app", AsyncMock(return_value=app))
    cancel = AsyncMock()
    monkeypatch.setattr(partner_apps, "cancel_purge", cancel)
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: store)
        )
    )

    await partner_apps.dev_install(
        app.id,
        partner_apps.DevInstallRequest(store_id=store.id),
        user_id=uuid4(),
        db=db,
    )

    cancel.assert_awaited_once_with(db, store.id, app.id)

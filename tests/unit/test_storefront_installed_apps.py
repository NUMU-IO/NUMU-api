"""The storefront payload's `installed_apps` must leave out hub-only NUMU Apps.

WhatsApp and the Inbox became NUMU Apps (apps plan, Phase 1), and the
migration installed them on stores that already used them. Nothing on a
storefront renders either one, so listing them in the public store payload
only told every visitor which tools the merchant uses — and it bypassed the
ff_numu_apps rollout flag that hides them everywhere else. Found by reading
vionne's live payload after the 2026-09-21 promote.
"""

from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from src.api.v1.routes.storefront.public import _read_installed_apps


class _CapturingSession:
    """Records the statement instead of running it; the kill switch reads on."""

    def __init__(self):
        self.statements = []

    async def scalar(self, _stmt):
        return None  # partner_apps_enabled(): no config row means on

    async def execute(self, stmt):
        self.statements.append(stmt)

        class _Rows:
            def all(self):
                return []

        return _Rows()


def _where_sql(session: _CapturingSession) -> str:
    stmt = session.statements[-1]
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_hub_only_numu_apps_never_reach_the_storefront_payload():
    session = _CapturingSession()

    assert asyncio.run(_read_installed_apps(session, store_id=uuid4())) == []

    sql = _where_sql(session)
    not_in = re.search(r"apps\.slug NOT IN \(([^)]*)\)", sql)
    assert not_in, sql
    assert {"'whatsapp'", "'inbox'"} <= {s.strip() for s in not_in.group(1).split(",")}


def test_pending_consent_installs_never_reach_the_storefront_payload():
    session = _CapturingSession()
    asyncio.run(_read_installed_apps(session, store_id=uuid4()))
    assert "app_installations.status = 'active'" in _where_sql(session)


def test_the_storefront_app_routes_apply_the_same_visibility_rules(monkeypatch):
    # The public /storefront/.../apps routes used to filter only on is_enabled,
    # so they listed pending_auth installs, hub-only NUMU Apps and Partner Apps
    # with the kill switch off, while the store payload hid all three.
    from src.api.v1.routes.storefront import apps as routes

    session = _CapturingSession()

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(routes, "AsyncSessionLocal", _Ctx)
    session.execute_one = None

    async def _execute(stmt):
        session.statements.append(stmt)

        class _Rows:
            def all(self):
                return []

            def one_or_none(self):
                return None

        return _Rows()

    session.execute = _execute
    store_id = uuid4()

    asyncio.run(routes.list_installed_apps(store_id))
    listed = _where_sql(session)
    try:
        asyncio.run(routes.get_installed_app(store_id, "some-app"))
    except Exception:
        pass
    detail = _where_sql(session)

    for sql in (listed, detail):
        assert "app_installations.status = 'active'" in sql
        assert re.search(r"apps\.slug NOT IN \([^)]*'whatsapp'", sql), sql


def test_other_installs_are_still_read():
    # The exclusion is a filter on the query, not an early return: apps with a
    # storefront surface (e.g. variant-swatches) must still be looked up.
    session = _CapturingSession()
    asyncio.run(_read_installed_apps(session, store_id=uuid4()))
    assert len(session.statements) == 1

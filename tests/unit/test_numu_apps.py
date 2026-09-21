"""NUMU Apps follow the install only behind ff_numu_apps.

With the flag off, WhatsApp and the Inbox must stay on for every store, as
they were before they became apps: that is what makes the flag the rollback.
With it on, only an installed AND enabled app is on.
"""

from uuid import uuid4

import pytest

from src.application.services.numu_apps import FLAG, app_enabled, purge_due

APP = uuid4()  # the catalog row exists
INSTALL = uuid4()


class _Result:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _Session:
    """Answers the single query with (feature_flags, is_enabled, app id,
    install id, manifest)."""

    def __init__(self, row):
        self.row = row

    async def execute(self, _query):
        return _Result(self.row)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (({}, None, APP, None, {}), True),  # flag off, never installed: on, as before
        ((None, None, APP, None, {}), True),  # tenant has no flags at all
        (({FLAG: False}, False, APP, INSTALL, {}), True),  # flag explicitly off
        (({FLAG: True}, True, APP, INSTALL, {}), True),  # installed and enabled (free)
        (({FLAG: True}, False, APP, INSTALL, {}), False),  # installed but disabled
        (({FLAG: True}, None, APP, None, {}), False),  # not installed
        # Flag on but the catalog row is missing (Sentry, api#636): nobody can
        # have uninstalled an app that doesn't exist, so it stays on.
        (({FLAG: True}, None, None, None, None), True),
        (None, True),  # unknown store: not this function's call
    ],
)
async def test_app_enabled(row, expected):
    assert await app_enabled(_Session(row), uuid4(), "whatsapp") is expected


class _PurgeSession:
    """Returns the due rows once, then records every DELETE it is asked for."""

    def __init__(self, due):
        self.due = due
        self.deleted = []

    async def execute(self, query):
        if query.is_select:
            return _Rows(self.due)
        self.deleted.append(query.table.name)
        return None


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_purge_due_deletes_each_apps_conversations_and_its_row():
    inbox_store, wa_store, other_store = uuid4(), uuid4(), uuid4()
    session = _PurgeSession([
        (uuid4(), inbox_store, "inbox"),
        (uuid4(), wa_store, "whatsapp"),
        (uuid4(), other_store, "variant-swatches"),
    ])

    stats = await purge_due(session)

    assert stats == {"purged": 3}
    assert session.deleted == [
        "message_threads",  # inbox: Messenger + Instagram threads
        "app_uninstalls",
        "whatsapp_conversations",  # whatsapp: chats, then its threads
        "message_threads",
        "app_uninstalls",
        "app_uninstalls",  # an app with no purger only loses its row
    ]
    # Never billing or access data.
    assert "message_log" not in session.deleted
    assert "whatsapp_access_requests" not in session.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize(("covered", "expected"), [(True, True), (False, False)])
async def test_a_priced_numu_app_is_on_only_while_paid_for(
    monkeypatch, covered, expected
):
    """Phase 7: NUMU may put a recurring price on a NUMU App; it then works
    only while the store's subscription covers today."""
    from src.application.services import app_billing

    install_id = uuid4()
    priced = {"pricing": {"plan": "recurring", "price_cents": 9900, "cycle": "monthly"}}

    async def sub_for(_db, iid, **_kw):
        assert iid == install_id
        return object()

    monkeypatch.setattr(app_billing, "subscription_for", sub_for)
    monkeypatch.setattr(app_billing, "covers", lambda _sub, _now: covered)
    session = _Session(({FLAG: True}, True, APP, install_id, priced))
    assert await app_enabled(session, uuid4(), "whatsapp") is expected

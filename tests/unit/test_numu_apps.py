"""NUMU Apps follow the install only behind ff_numu_apps.

With the flag off, WhatsApp and the Inbox must stay on for every store, as
they were before they became apps: that is what makes the flag the rollback.
With it on, only an installed AND enabled app is on.
"""

from uuid import uuid4

import pytest

from src.application.services.numu_apps import FLAG, app_enabled, purge_due


class _Result:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _Session:
    """Answers the single query with (tenant feature_flags, install.is_enabled)."""

    def __init__(self, row):
        self.row = row

    async def execute(self, _query):
        return _Result(self.row)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (({}, None), True),  # flag off, never installed: on, as before
        ((None, None), True),  # tenant has no flags at all
        (({FLAG: False}, False), True),  # flag explicitly off
        (({FLAG: True}, True), True),  # installed and enabled
        (({FLAG: True}, False), False),  # installed but disabled
        (({FLAG: True}, None), False),  # not installed
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

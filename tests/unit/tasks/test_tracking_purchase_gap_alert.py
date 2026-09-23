"""A store with orders but no server Purchase on a platform it tracks is a gap."""

import asyncio
import uuid
from types import SimpleNamespace

from src.infrastructure.messaging.tasks import tiktok_capi

HEALTHY, SILENT, UNTRACKED = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
BOTH = {
    "meta": {"pixel_id": "1", "api_enabled": True, "access_token": "t"},
    "tiktok": {"pixel_id": "2", "api_enabled": True, "access_token": "t"},
}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _Session:
    def __init__(self, results):
        self._results = iter(results)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _query):
        return _Result(next(self._results))


def test_reports_only_tracked_platforms_with_no_purchase(monkeypatch):
    stores = [
        SimpleNamespace(id=HEALTHY, settings={"tracking": BOTH}),
        SimpleNamespace(id=SILENT, settings={"tracking": BOTH}),
        SimpleNamespace(id=UNTRACKED, settings={}),
    ]
    session = _Session([
        [(HEALTHY, 3), (SILENT, 2), (UNTRACKED, 5)],  # orders per store
        [(HEALTHY, 3)],  # Meta Purchase rows
        [(HEALTHY, 1)],  # TikTok Purchase rows
        stores,
    ])
    monkeypatch.setattr(
        "src.infrastructure.database.connection.AsyncSessionLocal", lambda: session
    )

    async def _no_rls(_session):
        return None

    monkeypatch.setattr("src.infrastructure.tenancy.rls.enable_rls_bypass", _no_rls)

    gaps = asyncio.run(tiktok_capi._find_purchase_gaps())

    assert sorted((g["store_id"], g["platform"], g["orders"]) for g in gaps) == [
        (str(SILENT), "meta", 2),
        (str(SILENT), "tiktok", 2),
    ]


def test_reconnect_notice_is_one_per_platform_per_day(monkeypatch):
    sent = []

    async def _emit(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(
        "src.application.services.notification_feed.emit_notification_standalone",
        _emit,
    )
    store = str(uuid.uuid4())
    asyncio.run(tiktok_capi.notify_tracking_reconnect(store, "Meta"))
    asyncio.run(tiktok_capi.notify_tracking_reconnect(store, "TikTok"))

    assert [n["kind"] for n in sent] == ["tracking.reconnect_required"] * 2
    assert [n["data"]["details"] for n in sent] == ["Meta", "TikTok"]
    assert sent[0]["dedupe_key"] != sent[1]["dedupe_key"]
    assert sent[0]["dedupe_key"].startswith("tracking.reconnect:Meta:")

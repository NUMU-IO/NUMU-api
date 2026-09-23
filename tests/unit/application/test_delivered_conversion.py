"""COD delivery sends its own conversion once: Meta OrderDelivered, TikTok a
standard Purchase on the Offline Event Set (web-only keys stripped)."""

import asyncio
import uuid
from types import SimpleNamespace

from src.application.services import funnel_emit_service as svc
from src.infrastructure.messaging.tasks.tiktok_capi import build_capi_payload


def test_offline_envelope_keeps_only_contact_keys():
    payload = build_capi_payload(
        pixel_id="OFFLINE_SET",
        event_name="Purchase",
        event_time=1,
        event_id="delivered-1",
        hashed_user={"email": "e", "phone": "p", "ttclid": "t", "ttp": "x", "ip": "1"},
        properties={},
        event_source="offline",
    )
    assert payload["event_source"] == "offline"
    assert payload["event_source_id"] == "OFFLINE_SET"
    assert payload["data"][0]["user"] == {"email": "e", "phone": "p"}


def test_delivery_sends_its_conversion_once(monkeypatch):
    sent = []

    async def _meta(_s, _o, **kw):
        sent.append(("meta", kw["event_name"], kw["event_id"], kw["event_time_now"]))

    async def _tiktok(_s, _o, **kw):
        sent.append(("tiktok", kw["event_name"], kw["event_id"], kw["offline"]))

    monkeypatch.setattr(svc, "enqueue_meta_capi_event_for_order", _meta)
    monkeypatch.setattr(svc, "enqueue_tiktok_capi_event_for_order", _tiktok)

    class _Repo:
        session = None

        async def create(self, **_kw):
            return None

        async def update(self, _o):
            return None

    order = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        store_id=uuid.uuid4(),
        customer_id=None,
        order_number="ORD-1",
        total=100,
        currency="EGP",
        metadata={},
    )
    for _ in range(2):  # a replayed courier webhook must not resend
        asyncio.run(svc.emit_order_delivered(order, _Repo(), _Repo()))

    eid = f"delivered-{order.id}"
    assert sent == [
        ("meta", "OrderDelivered", eid, True),
        ("tiktok", "Purchase", eid, True),
    ]


def test_a_returned_order_sends_one_meta_refund(monkeypatch):
    from src.application.services import stock_service

    refunds = []
    results = iter([True, False])  # restock is idempotent: only the first counts

    async def _restock(_s, _o, reason=None):
        return next(results)

    async def _refund(_s, order):
        refunds.append(order.id)

    monkeypatch.setattr(stock_service, "restock_order", _restock)
    monkeypatch.setattr(stock_service, "enqueue_meta_capi_refund", _refund)
    order = SimpleNamespace(id=uuid.uuid4())

    for _ in range(2):
        asyncio.run(stock_service.try_restock_order(None, order, reason="returned"))

    assert refunds == [order.id]

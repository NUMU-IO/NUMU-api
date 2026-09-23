"""COD sends Purchase at creation; manual rails on proof approval; only
storefront checkouts (they alone carry the shopper's browser snapshot)."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from src.infrastructure.events.handlers import tracking_purchase_event_handler as h

STOREFRONT = {"user_agent": "Mozilla/5.0", "ip_address": "197.1.2.3"}


class _Result:
    def __init__(self, order):
        self._order = order

    def scalar_one_or_none(self):
        return self._order


class _Session:
    def __init__(self, order):
        self._order = order

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _query):
        return _Result(self._order)


@pytest.mark.parametrize(
    ("payment_method", "extra_data", "only_cod", "expected"),
    [
        ("cod", STOREFRONT, True, 2),  # COD storefront order, at creation
        (None, STOREFRONT, True, 2),  # no method recorded means COD
        ("instapay", STOREFRONT, True, 0),  # manual rail waits for approval
        ("instapay", STOREFRONT, False, 2),  # ... and fires on approval
        ("cod", {}, True, 0),  # merchant-created: no browser snapshot
    ],
)
def test_purchase_fires_only_for_real_storefront_sales(
    monkeypatch, payment_method, extra_data, only_cod, expected
):
    order = SimpleNamespace(
        id=uuid.uuid4(), payment_method=payment_method, extra_data=extra_data
    )
    monkeypatch.setattr(h, "AsyncSessionLocal", lambda: _Session(order))
    sent = []

    async def _record(_session, _order, *, event_name):
        sent.append(event_name)

    monkeypatch.setattr(h, "enqueue_meta_capi_event_for_order", _record)
    monkeypatch.setattr(h, "enqueue_tiktok_capi_event_for_order", _record)

    asyncio.run(h._send_purchase(order.id, only_cod=only_cod))

    assert sent == ["Purchase"] * expected

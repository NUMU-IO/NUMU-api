"""``/track order_completed`` → the server Purchase is built from the ORDER.

Both event logs are ``UNIQUE (store_id, event_id)`` and insert before
sending, and every Purchase path uses ``event_id = str(order.id)``. So the
confirmation page's ``order_completed`` — the first to arrive — is the only
server Purchase Meta / TikTok ever receive for an order; the webhook and
status-handler copies (order lines, shipping phone, customer email, click-id
snapshot) are dropped as duplicates.

These pin the fix: the ``/track`` leg now reuses the dispatchers' builders,
so the Purchase that actually reaches the platforms carries ``contents[]``
and the buyer's phone regardless of what the browser sent or whether the
session-fingerprint lookup found anything.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import src.api.v1.routes.storefront.tracking as tracking
from src.api.v1.routes.storefront.tracking import (
    _enrich_purchase_from_order,
    _maybe_enqueue_tiktok_capi,
)

PIXEL_ID = "D9GH5NRC77U5KEVKREF0"
REAL_IP = "197.54.10.20"
REAL_UA = "Mozilla/5.0 (iPhone)"
FINGERPRINT = "01J8ZQ4M0GDT4W2CJH8N6Y7X5R"
PRODUCT_ID = "6dc03192-f6a3-4100-b593-8cb185bc7bbe"


class _Order(SimpleNamespace):
    """An ORM-ish order: unknown attributes read as None, like a real row's
    nullable columns, so the dispatcher builders can `getattr` freely."""

    def __getattr__(self, name):  # only reached when normal lookup fails
        return None


def _store():
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        store_url="https://vionneeg.com",
        settings={
            "tracking": {
                "tiktok": {
                    "pixel_id": PIXEL_ID,
                    "pixel_enabled": True,
                    "api_enabled": True,
                }
            }
        },
    )


def _order(store):
    return _Order(
        id=uuid4(),
        store_id=store.id,
        customer_id=None,
        session_fingerprint="fp-at-checkout",
        line_items=[{"product_id": PRODUCT_ID, "quantity": 2, "unit_price": 25000}],
        shipping_address={
            "phone": "01001234567",
            "first_name": "Mona",
            "last_name": "Ali",
            "city": "Cairo",
            "country": "EG",
        },
        metadata={
            "ip_address": "10.0.0.1",
            "user_agent": "snapshot-UA",
            "ttclid": "tt.1",
        },
        total=50000,
        currency="EGP",
        paid_at=None,
    )


def _body(step_data, **kw):
    base = {
        "event_id": "order-uuid",
        "event_time": None,
        "page_url": "https://vionneeg.com/checkout/x/thank-you",
        "user_data": None,
        "ttclid": None,
        "ttp": None,
        "fingerprint": FINGERPRINT,
        "customer_id": None,
        "step_data": step_data,
        "opt_out": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    class _Task:
        @staticmethod
        def delay(**kwargs):
            calls.append(kwargs)

    import src.infrastructure.messaging.tasks.tiktok_capi as tt

    monkeypatch.setattr(tt, "tiktok_capi_send_event", _Task, raising=False)
    return calls


@pytest.fixture
def order_world(monkeypatch):
    """One store, one order; the DB-touching collaborators are stubbed."""
    store = _store()
    order = _order(store)

    async def _load(session, store_id, raw_order_id):
        return (
            order
            if (session is not None and str(raw_order_id) == str(order.id))
            else None
        )

    import src.application.services.meta_capi_purchase_dispatcher as meta_disp

    async def _fill(db, user_data, o):
        # What the real helper does when the customer row has an email: the
        # builder pre-seeds `email: None` (the address VO has no email), and
        # the customer record fills it.
        if not user_data.get("email"):
            user_data["email"] = "mona@example.com"

    async def _catalog(db, o):
        return {}

    monkeypatch.setattr(tracking, "_load_order_for_purchase", _load)
    monkeypatch.setattr(meta_disp, "fill_identity_from_customer", _fill)
    monkeypatch.setattr(meta_disp, "resolve_catalog_ids", _catalog)

    async def _no_session_identity(*a, **k):
        return None

    monkeypatch.setattr(tracking, "_resolve_session_identity", _no_session_identity)
    return store, order


async def _run(body, store, session=object()):
    await _maybe_enqueue_tiktok_capi(
        store=store,
        step="order_completed",
        body=body,
        ip=REAL_IP,
        user_agent=REAL_UA,
        session=session,
        landing_ttclid=None,
    )


@pytest.mark.asyncio
async def test_purchase_carries_order_lines_and_phone(sent, order_world):
    store, order = order_world
    await _run(
        _body({"order_id": str(order.id), "value": 500, "content_ids": [PRODUCT_ID]}),
        store,
    )

    assert len(sent) == 1
    ud, cd = sent[0]["user_data"], sent[0]["custom_data"]
    # Identity from the ORDER, not from a fingerprint lookup that may miss.
    assert ud["phone"] == "01001234567"
    assert ud["email"] == "mona@example.com"
    assert ud["first_name"] == "Mona"
    # Request-time signals still win over the checkout snapshot.
    assert ud["ip"] == REAL_IP
    assert ud["user_agent"] == REAL_UA
    assert ud["external_id"] == FINGERPRINT
    # Click id: absent on this request, filled from the snapshot.
    assert ud["ttclid"] == "tt.1"
    # Content from the lines — what the diagnostic actually reads.
    assert cd["contents"] == [{"id": PRODUCT_ID, "quantity": 2, "item_price": 250.0}]
    assert cd["content_ids"] == [PRODUCT_ID]
    assert cd["value"] == 500.0
    assert cd["order_id"] == str(order.id)


@pytest.mark.asyncio
async def test_order_beats_session_identity_but_request_signals_win(order_world):
    store, order = order_world
    user_data = {"phone": "stranger", "ip": REAL_IP, "ttclid": "from-cookie"}
    cd = await _enrich_purchase_from_order(
        platform="tiktok",
        session=object(),
        store=store,
        user_data=user_data,
        custom_data={"order_id": str(order.id)},
    )
    assert user_data["phone"] == "01001234567"  # the buyer of THIS order
    assert user_data["ip"] == REAL_IP  # not the snapshot's 10.0.0.1
    assert user_data["ttclid"] == "from-cookie"
    assert cd["num_items"] == 2


@pytest.mark.asyncio
async def test_meta_platform_uses_meta_builders(order_world):
    store, order = order_world
    user_data: dict = {}
    cd = await _enrich_purchase_from_order(
        platform="meta",
        session=object(),
        store=store,
        user_data=user_data,
        custom_data={"order_id": str(order.id)},
    )
    assert user_data["phone"] == "01001234567"
    assert cd["contents"][0]["id"] == PRODUCT_ID
    assert cd["currency"] == "EGP"


@pytest.mark.asyncio
async def test_unknown_or_foreign_order_leaves_browser_payload_alone(sent, order_world):
    store = order_world[0]
    browser = {"order_id": str(uuid4()), "value": 500, "content_ids": [PRODUCT_ID]}
    await _run(_body(browser), store)
    assert sent[0]["custom_data"] == browser
    assert "phone" not in sent[0]["user_data"]


@pytest.mark.asyncio
async def test_no_session_means_no_lookup(sent, order_world):
    store, order = order_world
    browser = {"order_id": str(order.id), "value": 500}
    await _run(_body(browser), store, session=None)
    assert sent[0]["custom_data"] == browser


@pytest.mark.asyncio
async def test_builder_failure_never_breaks_track(sent, order_world, monkeypatch):
    store, order = order_world

    async def _boom(**kw):
        raise RuntimeError("builder exploded")

    monkeypatch.setattr(tracking, "_enrich_purchase_from_order", _boom)
    browser = {"order_id": str(order.id), "value": 500}
    await _run(_body(browser), store)
    assert len(sent) == 1
    assert sent[0]["custom_data"] == browser

"""The merchant Kashier webhook must not act on an unverified call.

It used to log `webhook_signature_invalid_proceeding` and carry on, so a
forged `paymentStatus=SUCCESS` for a known order id marked the order paid:
the merchant would ship goods that were never paid for. The key it checked
(store.settings) was also not where checkout reads the key from, which is why
genuine calls "failed" and someone made failure non-fatal.

Also pinned: the replay nonce is taken only after verification. Taking it first
let a forged call carrying a real transaction id burn it, so the genuine
webhook was then rejected as a duplicate.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import quote, urlencode
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.v1.routes.webhooks import kashier as hook

KEY = "kashier-test-api-key"
ORDER_ID = uuid4()


def _payload(status="SUCCESS"):
    # Shape from developers.kashier.io/payment/webhook: signatureKeys sits
    # inside `data`, and `channel` has spaces, so the encoding matters.
    data = {
        "merchantOrderId": str(ORDER_ID),
        "kashierOrderId": "kx-1",
        "transactionId": "tx-123",
        "status": status,
        "amount": 100,
        "currency": "EGP",
        "channel": "online | e-commerce",
    }
    data["signatureKeys"] = sorted(k for k in data if k != "channel") + ["channel"]
    return {"event": "pay", "data": data}, data["signatureKeys"]


def _sign(body, key):
    # Kashier's signer: sorted keys, RFC 3986-encoded query string, HMAC-SHA256.
    data = body["data"]
    msg = urlencode(
        [(k, data[k]) for k in sorted(data["signatureKeys"])], quote_via=quote
    )
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()


class _Request:
    def __init__(self, raw: bytes):
        self._raw = raw

    async def body(self):
        return self._raw


class _Stop(Exception):
    """Raised by the first step AFTER verification: proves the call got past it."""


@pytest.fixture
def wired(monkeypatch):
    order = SimpleNamespace(
        id=ORDER_ID,
        order_number="1001",
        store_id=uuid4(),
        tenant_id=uuid4(),
        status="pending",
        mark_as_paid=lambda **kw: (_ for _ in ()).throw(AssertionError("marked paid")),
    )

    class _Orders:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _id):
            return order

        async def get_by_payment_id_for_update(self, _id):
            return None

    class _Stores:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _id):
            return SimpleNamespace(settings={})

    nonces = []

    class _Cache:
        async def set_if_absent(self, key, *_a, **_k):
            nonces.append(key)
            return True

    async def stop(*_a, **_k):
        raise _Stop

    monkeypatch.setattr(hook, "OrderRepository", _Orders)
    monkeypatch.setattr(hook, "StoreRepository", _Stores)
    monkeypatch.setattr(hook, "_cache_service", _Cache())
    monkeypatch.setattr(hook, "narrow_to_tenant", stop)
    return nonces


def _call(body, signature, keys, monkeypatch):
    async def candidate_keys(*_a, **_k):
        return keys

    monkeypatch.setattr(hook, "_candidate_api_keys", candidate_keys)
    raw = json.dumps(body).encode()
    return asyncio.run(
        hook.kashier_callback(_Request(raw), db=None, x_kashier_signature=signature)
    )


def test_a_forged_success_is_rejected_and_nothing_is_marked_paid(wired, monkeypatch):
    body, _ = _payload()
    with pytest.raises(HTTPException) as exc:
        _call(body, _sign(body, "attacker-guess"), [KEY], monkeypatch)
    assert exc.value.status_code == 401


def test_a_forged_call_does_not_burn_the_replay_nonce(wired, monkeypatch):
    body, _ = _payload()
    with pytest.raises(HTTPException):
        _call(body, "0" * 64, [KEY], monkeypatch)
    assert wired == [], "a forged call must not consume the genuine call's nonce"


def test_no_known_key_means_no_processing(wired, monkeypatch):
    body, _ = _payload()
    with pytest.raises(HTTPException) as exc:
        _call(body, _sign(body, KEY), [], monkeypatch)
    assert exc.value.status_code == 401


def test_a_genuine_call_signed_with_any_known_key_is_processed(wired, monkeypatch):
    body, _ = _payload()
    # Signed with the environment fallback key (checkout's second choice).
    with pytest.raises(_Stop):
        _call(body, _sign(body, "env-key"), ["tenant-key", "env-key"], monkeypatch)
    assert wired == ["kashier:processed:tx-123"]


def test_a_tampered_status_breaks_the_signature(wired, monkeypatch):
    body, _ = _payload(status="FAILED")
    signature = _sign(body, KEY)
    body["data"]["status"] = "SUCCESS"
    with pytest.raises(HTTPException) as exc:
        _call(body, signature, [KEY], monkeypatch)
    assert exc.value.status_code == 401


def test_the_browser_redirect_never_marks_an_order_paid(monkeypatch):
    # GET /webhooks/kashier/redirect?order_id=<id>&paymentStatus=SUCCESS used to
    # flip a pending order to paid with no verification at all: anyone could
    # open that URL. Only the signed server-to-server callback confirms payment.
    order = SimpleNamespace(
        id=ORDER_ID,
        order_number="1001",
        store_id=uuid4(),
        tenant_id=uuid4(),
        total=10000,
        payment_status=SimpleNamespace(value="pending"),
        mark_as_paid=lambda **kw: (_ for _ in ()).throw(AssertionError("marked paid")),
    )

    class _Orders:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _id):
            return order

        async def update(self, _order):
            raise AssertionError("order written")

    class _Stores:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _id):
            return SimpleNamespace(subdomain="shop")

    monkeypatch.setattr(hook, "OrderRepository", _Orders)
    monkeypatch.setattr(hook, "StoreRepository", _Stores)

    response = asyncio.run(
        hook.kashier_redirect(order_id=str(ORDER_ID), paymentStatus="SUCCESS", db=None)
    )

    assert response.status_code in (302, 307)
    assert "order-confirmation" in response.headers["location"]

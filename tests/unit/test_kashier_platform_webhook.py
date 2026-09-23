"""The platform Kashier webhook (wallet top-ups): verification and the nonce.

- A genuine call is verified with Kashier's documented algorithm (api #644).
- A call whose processing fails releases its replay nonce, so the retry
  Kashier sends is not rejected as a duplicate and the top-up still lands.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import quote, urlencode

import pytest
from fastapi import HTTPException

from src.api.v1.routes.webhooks import kashier_platform as hook

KEY = "platform-test-key"


class _Request:
    def __init__(self, raw: bytes):
        self._raw = raw

    async def body(self):
        return self._raw


def _signed(data: dict) -> tuple[bytes, str]:
    data = {**data, "signatureKeys": sorted(k for k in data)}
    msg = urlencode(
        [(k, data[k]) for k in sorted(data["signatureKeys"])], quote_via=quote
    )
    sig = hmac.new(KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return json.dumps({"event": "pay", "data": data}).encode(), sig


@pytest.fixture
def cache(monkeypatch):
    seen = SimpleNamespace(taken=[], released=[])

    class _Cache:
        async def set_if_absent(self, key, *_a, **_k):
            seen.taken.append(key)
            return True

        async def delete(self, key):
            seen.released.append(key)
            return True

    monkeypatch.setattr(hook, "_cache_service", _Cache())
    monkeypatch.setattr(hook.settings, "platform_kashier_api_key", KEY)
    monkeypatch.setattr(hook.settings, "platform_kashier_mid", "MID-1")
    return seen


class _BrokenDb:
    async def execute(self, _stmt):
        raise RuntimeError("database went away")


def test_a_bad_signature_is_401_and_takes_no_nonce(cache):
    raw, _ = _signed({"merchantOrderId": "WTOP-1", "transactionId": "tx-9"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            hook.kashier_platform_callback(
                _Request(raw), db=None, x_kashier_signature="00"
            )
        )
    assert exc.value.status_code == 401
    assert cache.taken == []


def test_a_failed_top_up_releases_its_nonce_for_the_retry(cache):
    raw, sig = _signed({
        "merchantOrderId": "WTOP-1",
        "transactionId": "tx-9",
        "status": "SUCCESS",
        "amount": 500,
    })
    with pytest.raises(RuntimeError):
        asyncio.run(
            hook.kashier_platform_callback(
                _Request(raw), db=_BrokenDb(), x_kashier_signature=sig
            )
        )
    assert cache.taken == ["kashier:platform:processed:tx-9"]
    assert cache.released == ["kashier:platform:processed:tx-9"]


@pytest.mark.parametrize("delete_fails_by", ["returning False", "raising"])
def test_a_failed_release_never_masks_the_original_error(
    cache, monkeypatch, delete_fails_by
):
    """Sentry on #653: Redis may refuse the delete, or the delete may raise.
    The original error must still propagate (so Kashier retries), and the
    failed release is logged."""
    logged = []

    async def refuse(_key):
        if delete_fails_by == "raising":
            raise ConnectionError("redis went away")
        return False

    class _Log:
        def bind(self, **_kw):
            return self

        def info(self, *_a, **_kw):
            pass

        def warning(self, *_a, **_kw):
            pass

        def error(self, event, **kw):
            logged.append((event, kw))

    monkeypatch.setattr(hook._cache_service, "delete", refuse)
    monkeypatch.setattr(hook, "logger", _Log())
    raw, sig = _signed({
        "merchantOrderId": "WTOP-1",
        "transactionId": "tx-9",
        "status": "SUCCESS",
    })
    with pytest.raises(RuntimeError):
        asyncio.run(
            hook.kashier_platform_callback(
                _Request(raw), db=_BrokenDb(), x_kashier_signature=sig
            )
        )
    assert logged == [
        (
            "kashier_nonce_release_failed",
            {"nonce_key": "kashier:platform:processed:tx-9"},
        )
    ]


class _IntentDb:
    """Returns one subscription intent for the lookup; records commits."""

    def __init__(self, intent):
        self.intent = intent
        self.commits = 0

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.intent)

    async def commit(self):
        self.commits += 1


def _sub_intent(status: str = "awaiting_proof"):
    return SimpleNamespace(id="i-1", tenant_id="t-1", amount_cents=25000, status=status)


def _run_sub(cache, monkeypatch, intent, *, status="SUCCESS", amount="250.00"):
    activated = []

    async def fake_activate(_db, *, intent):
        activated.append(intent.status)
        intent.status = "succeeded"
        return SimpleNamespace(activated=True)

    monkeypatch.setattr(hook, "activate_verified_subscription_payment", fake_activate)
    raw, sig = _signed({
        "merchantOrderId": "SUB-ABC123",
        "transactionId": "tx-sub",
        "status": status,
        "amount": amount,
    })
    db = _IntentDb(intent)
    result = asyncio.run(
        hook.kashier_platform_callback(_Request(raw), db=db, x_kashier_signature=sig)
    )
    return result, activated


def test_a_paid_card_subscription_activates_the_plan(cache, monkeypatch):
    result, activated = _run_sub(cache, monkeypatch, _sub_intent())
    assert result["status"] == "processed"
    assert activated == ["awaiting_proof"]


def test_a_late_payment_reopens_an_expired_intent(cache, monkeypatch):
    _, activated = _run_sub(cache, monkeypatch, _sub_intent("expired"))
    assert activated == ["awaiting_proof"]


def test_a_declined_or_short_payment_never_activates(cache, monkeypatch):
    _, declined = _run_sub(cache, monkeypatch, _sub_intent(), status="FAILURE")
    _, short = _run_sub(cache, monkeypatch, _sub_intent(), amount="1.00")
    assert declined == [] and short == []


def test_direct_card_order_is_signed_like_kashiers_docs():
    from src.infrastructure.external_services.kashier import KashierPaymentService

    params = KashierPaymentService(
        mid="MID-1-2", api_key=KEY, mode="live"
    ).direct_payment_params(
        reference="SUB-ABC123",
        amount_cents=25000,
        currency="EGP",
        description="d",
        webhook_url="https://w",
        redirect_url="https://r",
    )
    expected = hmac.new(
        KEY.encode(), b"/?payment=MID-1-2.SUB-ABC123.250.00.EGP", hashlib.sha256
    ).hexdigest()
    assert params["hash"] == expected
    assert params["body"]["order"]["amount"] == "250.00"
    assert params["endpoint"] == "https://fep.kashier.io/v3/orders/"
    assert "paymentMethod" not in params["body"]

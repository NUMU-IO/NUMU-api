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

"""Unit tests for Paymob completion verification (payment-link hardening)."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.services import shopify_paymob_verification as svc

HMAC_SECRET = "test-hmac-secret"


def _sign(payload: dict) -> str:
    """Real Paymob HMAC-SHA512 over the documented ordered fields."""

    def _str(val):
        if isinstance(val, bool):
            return "true" if val else "false"
        return str(val) if val is not None else ""

    obj = payload.get("obj", {})
    concatenated = "".join([
        _str(obj.get("amount_cents", "")),
        _str(obj.get("created_at", "")),
        _str(obj.get("currency", "")),
        _str(obj.get("error_occured", "")),
        _str(obj.get("has_parent_transaction", "")),
        _str(obj.get("id", "")),
        _str(obj.get("integration_id", "")),
        _str(obj.get("is_3d_secure", "")),
        _str(obj.get("is_auth", "")),
        _str(obj.get("is_capture", "")),
        _str(obj.get("is_refunded", "")),
        _str(obj.get("is_standalone_payment", "")),
        _str(obj.get("is_voided", "")),
        _str(obj.get("order", {}).get("id", "")),
        _str(obj.get("owner", "")),
        _str(obj.get("pending", "")),
        _str(obj.get("source_data", {}).get("pan", "")),
        _str(obj.get("source_data", {}).get("sub_type", "")),
        _str(obj.get("source_data", {}).get("type", "")),
        _str(obj.get("success", "")),
    ])
    return hmac_mod.new(
        HMAC_SECRET.encode(), concatenated.encode(), hashlib.sha512
    ).hexdigest()


def _payload(*, amount_cents: int = 15000, success: bool = True) -> dict:
    return {
        "type": "TRANSACTION",
        "obj": {
            "id": 987654,
            "amount_cents": amount_cents,
            "created_at": "2026-08-22T10:00:00",
            "currency": "EGP",
            "error_occured": False,
            "has_parent_transaction": False,
            "integration_id": 111,
            "is_3d_secure": True,
            "is_auth": False,
            "is_capture": False,
            "is_refunded": False,
            "is_standalone_payment": True,
            "is_voided": False,
            "order": {"id": 555},
            "owner": 42,
            "pending": False,
            "source_data": {"pan": "1234", "sub_type": "MasterCard", "type": "card"},
            "success": success,
        },
    }


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, store):
        self._store = store

    async def execute(self, _query):
        return _FakeResult(self._store)


@pytest.fixture
def store_with_creds(monkeypatch):
    """A store whose decrypted creds contain our HMAC secret."""

    async def _fake_creds(_settings):
        return {"hmac_secret": HMAC_SECRET}

    import src.infrastructure.external_services.paymob.payment_service as ps

    monkeypatch.setattr(ps, "get_merchant_paymob_credentials", _fake_creds)
    return SimpleNamespace(settings={"payment": {"paymob": {"x": 1}}})


@pytest.mark.asyncio
async def test_accepts_valid_signature_and_amount(store_with_creds):
    payload = _payload(amount_cents=15000)
    ok, reason = await svc.verify_paymob_completion(
        _FakeSession(store_with_creds),
        store_id=uuid4(),
        expected_amount_cents=15000,
        paymob_payload=payload,
        paymob_hmac=_sign(payload),
    )
    assert ok is True
    assert reason == "ok"


@pytest.mark.asyncio
async def test_rejects_tampered_payload(store_with_creds):
    payload = _payload(amount_cents=15000)
    signature = _sign(payload)
    payload["obj"]["amount_cents"] = 1  # tamper after signing
    ok, reason = await svc.verify_paymob_completion(
        _FakeSession(store_with_creds),
        store_id=uuid4(),
        expected_amount_cents=1,
        paymob_payload=payload,
        paymob_hmac=signature,
    )
    assert ok is False
    assert reason == svc.REASON_INVALID_SIGNATURE


@pytest.mark.asyncio
async def test_rejects_unsuccessful_transaction(store_with_creds):
    payload = _payload(success=False)
    ok, reason = await svc.verify_paymob_completion(
        _FakeSession(store_with_creds),
        store_id=uuid4(),
        expected_amount_cents=15000,
        paymob_payload=payload,
        paymob_hmac=_sign(payload),
    )
    assert ok is False
    assert reason == svc.REASON_NOT_SUCCESSFUL


@pytest.mark.asyncio
async def test_rejects_amount_mismatch(store_with_creds):
    """A real 1-EGP transaction must not complete a 150-EGP session."""
    payload = _payload(amount_cents=100)
    ok, reason = await svc.verify_paymob_completion(
        _FakeSession(store_with_creds),
        store_id=uuid4(),
        expected_amount_cents=15000,
        paymob_payload=payload,
        paymob_hmac=_sign(payload),
    )
    assert ok is False
    assert reason == svc.REASON_AMOUNT_MISMATCH


@pytest.mark.asyncio
async def test_rejects_when_store_missing_or_unconfigured(monkeypatch):
    ok, reason = await svc.verify_paymob_completion(
        _FakeSession(None),
        store_id=uuid4(),
        expected_amount_cents=100,
        paymob_payload=_payload(),
        paymob_hmac="deadbeef",
    )
    assert ok is False
    assert reason == svc.REASON_NOT_CONFIGURED

    from src.core.exceptions import PaymentError

    async def _no_creds(_settings):
        raise PaymentError("not configured")

    import src.infrastructure.external_services.paymob.payment_service as ps

    monkeypatch.setattr(ps, "get_merchant_paymob_credentials", _no_creds)
    ok, reason = await svc.verify_paymob_completion(
        _FakeSession(SimpleNamespace(settings={})),
        store_id=uuid4(),
        expected_amount_cents=100,
        paymob_payload=_payload(),
        paymob_hmac="deadbeef",
    )
    assert ok is False
    assert reason == svc.REASON_NOT_CONFIGURED


def test_signature_survives_json_roundtrip():
    """The route re-serializes the payload dict; verify_webhook_signature
    json.loads it back — the ordered-field concat must be stable."""
    payload = _payload()
    roundtripped = json.loads(json.dumps(payload))
    assert _sign(roundtripped) == _sign(payload)

"""Payment callbacks must be signed by the gateway.

Paymob and Fawaterak used to log a bad signature and carry on, so a forged
"success" body marked any order paid. They now refuse it with 401, before
touching the order or the replay nonce.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.v1.routes.webhooks import fawaterak, paymob
from src.infrastructure.external_services.fawaterak.payment_service import (
    FawaterakPaymentService,
)

VENDOR_KEY = "vendor-secret"


class _Request:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()

    async def body(self) -> bytes:
        return self._body


def _wire(monkeypatch, module, order):
    class _Orders:
        def __init__(self, _db):
            pass

    class _Stores:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _id):
            return SimpleNamespace(id=order.store_id, settings={})

    async def _resolve(*_args):
        return order

    touched = []

    async def _narrow(*_args):
        touched.append("narrowed")

    monkeypatch.setattr(module, "OrderRepository", _Orders)
    monkeypatch.setattr(module, "StoreRepository", _Stores)
    monkeypatch.setattr(module, "_resolve_order", _resolve)
    monkeypatch.setattr(module, "narrow_to_tenant", _narrow)
    monkeypatch.setattr(module, "_cache_service", None)
    return touched


def _order():
    return SimpleNamespace(
        id=uuid4(), store_id=uuid4(), tenant_id=uuid4(), order_number="ORD-1"
    )


@pytest.mark.asyncio
async def test_paymob_rejects_a_forged_success(monkeypatch):
    touched = _wire(monkeypatch, paymob, _order())

    async def _creds(_settings):
        return {"hmac_secret": "real-secret"}

    monkeypatch.setattr(paymob, "get_merchant_paymob_credentials", _creds)
    body = {
        "type": "TRANSACTION",
        "obj": {"id": 1, "success": True, "order": {"id": 9}},
    }

    with pytest.raises(HTTPException) as exc:
        await paymob.paymob_callback(_Request(body), db=object(), hmac="forged")

    assert exc.value.status_code == 401
    assert touched == [], "the order must not be touched"


@pytest.mark.asyncio
async def test_fawaterak_rejects_a_forged_paid(monkeypatch):
    order = _order()
    touched = _wire(monkeypatch, fawaterak, order)
    body = {
        "invoice_id": 1,
        "invoice_key": "k",
        "payment_method": "card",
        "invoice_status": "paid",
        "hashKey": "forged",
        "pay_load": str(order.id),
    }

    with pytest.raises(HTTPException) as exc:
        await fawaterak.fawaterak_callback(_Request(body), db=object())

    assert exc.value.status_code == 401
    assert touched == []


def test_fawaterak_verifier_accepts_only_a_real_signature():
    svc = FawaterakPaymentService(vendor_key=VENDOR_KEY)
    body = {"invoice_id": 7, "invoice_key": "abc", "payment_method": "card"}
    good = hmac.new(
        VENDOR_KEY.encode(),
        b"InvoiceId=7&InvoiceKey=abc&PaymentMethod=card",
        hashlib.sha256,
    ).hexdigest()

    assert svc.verify_webhook_signature(
        json.dumps({**body, "hashKey": good}).encode(), ""
    )
    assert (
        svc.verify_webhook_signature(json.dumps({**body, "hashKey": "x"}).encode(), "")
        is None
    )
    # A refund shape carries no hash and used to pass through unsigned.
    refund = {"transactionId": 1, "status": "approved", "reason": "x"}
    assert svc.verify_webhook_signature(json.dumps(refund).encode(), "") is None

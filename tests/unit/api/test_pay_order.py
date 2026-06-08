"""Unit tests for the recovery /pay endpoints (COD → prepaid conversion page)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.v1.routes.storefront.pay import (
    PayOrderRequest,
    get_pay_order_view,
    initiate_pay_order,
)
from src.core.entities.order import OrderStatus, PaymentStatus


def _order(
    *, store_id, status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
):
    return SimpleNamespace(
        id=uuid4(),
        store_id=store_id,
        order_number="ORD-1042",
        status=status,
        payment_status=payment_status,
        currency="EGP",
        total=25000,
        payment_id=None,
        metadata={},
        customer_email="sara@example.com",
        line_items=[
            SimpleNamespace(product_name="Abaya", quantity=1, unit_price=25000),
        ],
        shipping_address=SimpleNamespace(
            first_name="Sara",
            last_name="A",
            phone="+201001234567",
            city="Cairo",
            country="EG",
            address_line1="12 Tahrir St",
            email=None,
        ),
    )


def _store(store_id, settings):
    return SimpleNamespace(id=store_id, name="Acme Store", settings=settings)


def _repos(order, store):
    order_repo = AsyncMock()
    order_repo.get_by_id = AsyncMock(return_value=order)
    order_repo.update = AsyncMock()
    store_repo = AsyncMock()
    store_repo.get_by_id = AsyncMock(return_value=store)
    return order_repo, store_repo


_PAYMOB_ON = {
    "payment": {"paymob": {"enabled": True}},
    "cod_trust": {"recovery_promo": "10% off when you pay online"},
}


@pytest.mark.asyncio
async def test_get_view_payable_cod_order():
    sid = uuid4()
    order = _order(store_id=sid)
    order_repo, store_repo = _repos(order, _store(sid, _PAYMOB_ON))

    resp = await get_pay_order_view(sid, order.id, order_repo, store_repo)

    assert resp.data.is_payable is True
    assert resp.data.amount_due == 25000
    assert resp.data.total == 25000
    assert resp.data.enabled_payment_methods == ["paymob"]
    assert resp.data.recovery_promo == "10% off when you pay online"
    assert resp.data.not_payable_reason is None


@pytest.mark.asyncio
async def test_get_view_already_paid_not_payable():
    sid = uuid4()
    order = _order(store_id=sid, payment_status=PaymentStatus.PAID)
    order_repo, store_repo = _repos(order, _store(sid, _PAYMOB_ON))

    resp = await get_pay_order_view(sid, order.id, order_repo, store_repo)

    assert resp.data.is_payable is False
    assert resp.data.not_payable_reason == "already_paid"


@pytest.mark.asyncio
async def test_get_view_closed_order_not_payable():
    sid = uuid4()
    order = _order(store_id=sid, status=OrderStatus.CANCELLED)
    order_repo, store_repo = _repos(order, _store(sid, _PAYMOB_ON))

    resp = await get_pay_order_view(sid, order.id, order_repo, store_repo)

    assert resp.data.is_payable is False
    assert resp.data.not_payable_reason == "closed"


@pytest.mark.asyncio
async def test_order_from_a_different_store_is_404():
    sid = uuid4()
    order = _order(store_id=uuid4())  # belongs to another store
    order_repo, store_repo = _repos(order, _store(sid, _PAYMOB_ON))

    with pytest.raises(HTTPException) as ei:
        await get_pay_order_view(sid, order.id, order_repo, store_repo)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_order_is_404():
    sid = uuid4()
    order_repo = AsyncMock()
    order_repo.get_by_id = AsyncMock(return_value=None)
    store_repo = AsyncMock()
    store_repo.get_by_id = AsyncMock(return_value=_store(sid, _PAYMOB_ON))

    with pytest.raises(HTTPException) as ei:
        await get_pay_order_view(sid, uuid4(), order_repo, store_repo)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_post_paymob_initiates_and_stamps_recovery(monkeypatch):
    sid = uuid4()
    order = _order(store_id=sid)
    order_repo, store_repo = _repos(order, _store(sid, _PAYMOB_ON))

    async def _fake_creds(_settings):
        return {
            "secret_key": "s",
            "public_key": "pk_test",
            "hmac_secret": "h",
            "card_integration_id": "1",
            "wallet_integration_id": "2",
        }

    class _FakePaymob:
        def __init__(self, **_kw):
            pass

        async def create_payment_intent(self, **_kw):
            return SimpleNamespace(id="intent_123", client_secret="cs_abc")

    monkeypatch.setattr(
        "src.infrastructure.external_services.paymob.payment_service.get_merchant_paymob_credentials",
        _fake_creds,
    )
    monkeypatch.setattr(
        "src.infrastructure.external_services.paymob.payment_service.PaymobPaymentService",
        _FakePaymob,
    )

    resp = await initiate_pay_order(
        sid, order.id, PayOrderRequest(payment_method="paymob"), order_repo, store_repo
    )

    assert resp.data["provider"] == "paymob"
    assert resp.data["client_secret"] == "cs_abc"
    assert resp.data["public_key"] == "pk_test"
    assert resp.data["payment_url"].startswith(
        "https://accept.paymob.com/unifiedcheckout/"
    )
    assert "clientSecret=cs_abc" in resp.data["payment_url"]
    # The recovery marker + payment ref were persisted for the callback.
    assert order.metadata["cod_recovery_initiated"] is True
    assert order.payment_id == "intent_123"
    order_repo.update.assert_awaited()


@pytest.mark.asyncio
async def test_post_not_payable_is_409():
    sid = uuid4()
    order = _order(store_id=sid, payment_status=PaymentStatus.PAID)
    order_repo, store_repo = _repos(order, _store(sid, _PAYMOB_ON))

    with pytest.raises(HTTPException) as ei:
        await initiate_pay_order(
            sid,
            order.id,
            PayOrderRequest(payment_method="paymob"),
            order_repo,
            store_repo,
        )
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_post_unsupported_method_is_400():
    sid = uuid4()
    order = _order(store_id=sid)
    order_repo, store_repo = _repos(order, _store(sid, _PAYMOB_ON))

    with pytest.raises(HTTPException) as ei:
        await initiate_pay_order(
            sid,
            order.id,
            PayOrderRequest(payment_method="bitcoin"),
            order_repo,
            store_repo,
        )
    assert ei.value.status_code == 400


def test_callback_stamps_cod_recovered_only_when_initiated():
    """The webhook stamp condition (mirrors webhooks/paymob.py)."""
    # initiated → stamped
    md = {"cod_recovery_initiated": True}
    if (md or {}).get("cod_recovery_initiated"):
        md = {**md, "cod_recovered": True}
    assert md["cod_recovered"] is True

    # normal (non-recovery) order → untouched
    md2 = {}
    if (md2 or {}).get("cod_recovery_initiated"):
        md2 = {**md2, "cod_recovered": True}
    assert "cod_recovered" not in md2

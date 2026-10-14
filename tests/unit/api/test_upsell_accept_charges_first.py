"""Post-purchase upsell: the saved card is charged before the order changes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

import src.application.services.stock_service as stock_service
import src.infrastructure.external_services.paymob.payment_service as paymob_mod
from src.api.v1.routes.storefront import upsell as route
from src.api.v1.schemas.tenant.upsell import AcceptUpsellRequest
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)


def _call(monkeypatch, *, status=OrderStatus.CONFIRMED, charge_ok=False):
    store_id, customer_id = uuid4(), uuid4()
    order = Order(
        id=uuid4(),
        store_id=store_id,
        tenant_id=uuid4(),
        customer_id=customer_id,
        order_number="ORD-000001",
        shipping_address=OrderShippingAddress(
            first_name="Mona",
            last_name="Adel",
            address_line1="1 St",
            city="Cairo",
            country="EG",
        ),
        line_items=[
            OrderLineItem(
                product_id=uuid4(),
                product_name="Tee",
                unit_price=10_000,
                total_price=10_000,
            )
        ],
        status=status,
        payment_status=PaymentStatus.PAID,
        subtotal=10_000,
        total=10_000,
        currency="EGP",
    )
    order_repo = AsyncMock()
    order_repo.get_by_id.return_value = order
    rule = SimpleNamespace(
        id=uuid4(),
        is_active=True,
        store_id=store_id,
        max_uses=None,
        uses_count=0,
        offer_product_id=uuid4(),
        discount_type="fixed",
        discount_value=1_000,
    )
    upsell_repo = AsyncMock()
    upsell_repo.get_by_id.return_value = rule
    product = MagicMock(id=rule.offer_product_id, quantity=5, images=[])
    product.name = "Socks"
    product.effective_price.return_value.cents = 5_000
    product_repo = AsyncMock()
    product_repo.get_by_id.return_value = product
    store_repo = AsyncMock()
    store_repo.get_by_id.return_value = SimpleNamespace(settings={})
    saved = SimpleNamespace(gateway="paymob", card_token="tok")
    result = MagicMock()
    result.scalar_one_or_none.return_value = saved
    db = AsyncMock()
    db.execute.return_value = result

    async def creds(_settings):
        return {"secret_key": "sk"}

    async def charge(self, **_):
        return SimpleNamespace(success=charge_ok, error_message="declined")

    debit = AsyncMock(return_value=(True, None, {"product_id": "p", "quantity": 1}))
    monkeypatch.setattr(paymob_mod, "get_merchant_paymob_credentials", creds)
    monkeypatch.setattr(paymob_mod.PaymobPaymentService, "charge_saved_token", charge)
    monkeypatch.setattr(stock_service, "debit_order_line", debit)

    coro = route.accept_upsell_offer(
        store_id=store_id,
        body=AcceptUpsellRequest(order_id=str(order.id), rule_id=str(rule.id)),
        customer=SimpleNamespace(id=customer_id),
        order_repo=order_repo,
        upsell_repo=upsell_repo,
        product_repo=product_repo,
        store_repo=store_repo,
        db=db,
    )
    return coro, order_repo, upsell_repo, debit, order


@pytest.mark.asyncio
async def test_declined_charge_changes_nothing(monkeypatch):
    coro, order_repo, upsell_repo, debit, _ = _call(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await coro

    assert exc.value.status_code == 402
    order_repo.update.assert_not_called()
    upsell_repo.increment_uses.assert_not_called()
    debit.assert_not_called()


@pytest.mark.asyncio
async def test_shipped_order_cannot_take_an_upsell(monkeypatch):
    coro, order_repo, *_ = _call(
        monkeypatch, status=OrderStatus.SHIPPED, charge_ok=True
    )

    with pytest.raises(HTTPException) as exc:
        await coro

    assert exc.value.status_code == 409
    order_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_paid_upsell_adds_item_and_debits_stock(monkeypatch):
    coro, order_repo, upsell_repo, debit, _ = _call(monkeypatch, charge_ok=True)

    await coro

    debit.assert_awaited_once()
    saved_order = order_repo.update.call_args.args[0]
    assert saved_order.total == 10_000 + 4_000
    assert saved_order.line_items[-1].product_name == "Socks"
    assert saved_order.metadata["stock_debited"]["lines"][-1]["product_id"] == "p"
    upsell_repo.increment_uses.assert_awaited_once()

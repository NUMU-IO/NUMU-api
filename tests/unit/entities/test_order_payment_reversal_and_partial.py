"""Order entity: un-mark paid + partial acceptance maths."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)


def _order(*, paid: bool = True, method: str = "cod") -> Order:
    store = uuid4()
    items = [
        OrderLineItem(
            product_id=uuid4(),
            product_name="A",
            quantity=2,
            unit_price=1000,
            total_price=2000,
        ),
        OrderLineItem(
            product_id=uuid4(),
            product_name="B",
            quantity=1,
            unit_price=5000,
            total_price=4000,
        ),  # discounted
    ]
    o = Order(
        id=uuid4(),
        store_id=store,
        tenant_id=store,
        customer_id=uuid4(),
        order_number="ORD-1",
        line_items=items,
        shipping_address=OrderShippingAddress(
            first_name="Y",
            last_name="S",
            address_line1="1",
            city="Cairo",
            country="EG",
            phone="+201000000000",
        ),
        subtotal=6000,
        shipping_cost=500,
        total=6500,
        currency="EGP",
        payment_method=method,
        status=OrderStatus.DELIVERED,
    )
    if paid:
        o.payment_status = PaymentStatus.PAID
        o.paid_at = datetime.now(UTC)
        o.payment_id = "manual"
    return o


def test_reverse_payment_clears_paid_state_and_keeps_history():
    o = _order()
    amount = o.reverse_payment(reason="marked by mistake")
    assert amount == 6500
    assert o.payment_status == PaymentStatus.PENDING
    assert o.paid_at is None and o.payment_id is None
    assert o.status == OrderStatus.DELIVERED  # payment-plane only
    hist = o.metadata["payment_history"]
    assert hist[-1]["from"] == "paid" and hist[-1]["reason"] == "marked by mistake"


def test_reverse_payment_requires_paid():
    with pytest.raises(ValueError):
        _order(paid=False).reverse_payment()


def test_partial_acceptance_uses_effective_unit_price_and_keeps_shipping():
    o = _order()
    summary = o.record_partial_acceptance({0: 1, 1: 1}, reason="customer kept one A")
    # line 0: 1 × 1000; line 1: discounted line, effective 4000 × 1
    assert summary["returned_value_cents"] == 5000
    assert summary["collected_total_cents"] == 1500  # 6500 − 5000 (shipping stays)
    assert o.collected_total == 1500
    assert o.collectible_total == 1500
    assert o.metadata["partial_acceptance"]["lines"][1]["value_cents"] == 4000
    assert o.reverse_payment() == 1500  # reversal uses the collected amount


@pytest.mark.parametrize("returned", [{}, {5: 1}, {0: 3}, {0: 0}])
def test_partial_acceptance_validation(returned):
    with pytest.raises(ValueError):
        _order().record_partial_acceptance(returned)

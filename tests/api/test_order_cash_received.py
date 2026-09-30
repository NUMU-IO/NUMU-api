"""COD cash received from the courier: paid-with-courier vs paid-and-collected.

Runs the real UPDATE and list filter against the schema-built test DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api.v1.routes.stores.orders import (
    CashReceivedRequest,
    mark_orders_cash_received,
)
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.infrastructure.repositories.order_repository import OrderRepository

STORE_ID = uuid4()
TENANT_ID = uuid4()


def _order(payment_status: PaymentStatus, store_id=STORE_ID) -> Order:
    return Order(
        id=uuid4(),
        store_id=store_id,
        tenant_id=TENANT_ID,
        customer_id=uuid4(),
        order_number=f"ORD-{uuid4().hex[:6].upper()}",
        line_items=[
            OrderLineItem(
                product_id=uuid4(),
                product_name="Book",
                quantity=1,
                unit_price=84_000,
                total_price=84_000,
            )
        ],
        shipping_address=OrderShippingAddress(
            first_name="Test",
            last_name="Customer",
            address_line1="10 Tahrir Square",
            city="Cairo",
            country="EG",
        ),
        status=OrderStatus.DELIVERED,
        payment_status=payment_status,
        subtotal=84_000,
        total=84_000,
        currency="EGP",
        payment_method="cod",
    )


async def _mark(session, ids, received=True) -> int:
    result = await mark_orders_cash_received(
        CashReceivedRequest(order_ids=ids, received=received),
        store=SimpleNamespace(id=STORE_ID),
        db=session,
    )
    return result.data.updated


async def _ids(repo, received: bool) -> set:
    rows = await repo.get_by_store(STORE_ID, cash_received=received)
    assert await repo.count_by_store(STORE_ID, cash_received=received) == len(rows)
    return {o.id for o in rows}


@pytest.mark.asyncio
async def test_cash_received_marks_paid_orders_only_and_undoes(test_session):
    repo = OrderRepository(test_session)
    paid_a = await repo.create(_order(PaymentStatus.PAID))
    paid_b = await repo.create(_order(PaymentStatus.PAID))
    unpaid = await repo.create(_order(PaymentStatus.PENDING))
    other_store = await repo.create(_order(PaymentStatus.PAID, store_id=uuid4()))
    await test_session.commit()

    # Paid at the door, cash still with the courier.
    assert await _ids(repo, received=False) == {paid_a.id, paid_b.id}

    everything = [paid_a.id, paid_b.id, unpaid.id, other_store.id]
    assert await _mark(test_session, everything) == 2
    # Idempotent: already-received orders are skipped.
    assert await _mark(test_session, everything) == 0
    test_session.expire_all()
    assert await _ids(repo, received=True) == {paid_a.id, paid_b.id}
    assert await _ids(repo, received=False) == set()

    assert await _mark(test_session, [paid_b.id], received=False) == 1
    test_session.expire_all()
    assert await _ids(repo, received=False) == {paid_b.id}


def test_unmarking_paid_clears_cash_received():
    from datetime import UTC, datetime

    order = _order(PaymentStatus.PAID)
    order.cash_received_at = datetime.now(UTC)
    order.reverse_payment(reason="wrong order")
    assert order.cash_received_at is None

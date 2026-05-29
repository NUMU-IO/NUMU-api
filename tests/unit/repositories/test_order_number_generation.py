"""Order-number generation must never reuse an existing number.

Regression for the duplicate ``ORD-000033`` incident: the old
``count(*) + 1`` scheme reused a number whenever an order had been deleted
(count drops below the highest existing number). The generator now derives
the next number from ``MAX`` so it is always strictly greater than every
existing number for the store.
"""

import uuid
from datetime import UTC, datetime

import pytest

from src.core.entities.order import OrderStatus
from src.infrastructure.database.models import OrderModel
from src.infrastructure.repositories.order_repository import OrderRepository


def _order(store_id: uuid.UUID, number: str) -> OrderModel:
    now = datetime.now(UTC)
    return OrderModel(
        id=uuid.uuid4(),
        store_id=store_id,
        tenant_id=store_id,
        customer_id=uuid.uuid4(),
        order_number=number,
        status=OrderStatus.PENDING,
        line_items=[],
        shipping_address={
            "first_name": "Test",
            "last_name": "User",
            "address_line1": "1 St",
            "city": "Cairo",
            "country": "EG",
            "phone": "+201111111111",
        },
        billing_address=None,
        subtotal=1000,
        shipping_cost=0,
        tax_amount=0,
        discount_amount=0,
        total=1000,
        currency="EGP",
        created_at=now,
        updated_at=now,
        version=1,
    )


@pytest.mark.asyncio
async def test_first_order_number_is_one(test_session):
    repo = OrderRepository(test_session)
    assert await repo.get_next_order_number(uuid.uuid4()) == "ORD-000001"


@pytest.mark.asyncio
async def test_next_number_uses_max_not_count(test_session):
    """With a gap (deleted early order), count+1 would collide; MAX+1 won't."""
    store_id = uuid.uuid4()
    for number in ("ORD-000001", "ORD-000002", "ORD-000005"):
        test_session.add(_order(store_id, number))
    await test_session.flush()

    repo = OrderRepository(test_session)
    # Only 3 orders exist, so count(*)+1 == ORD-000004 — which is below the
    # existing max and risks colliding with future inserts. MAX+1 == 000006.
    assert await repo.get_next_order_number(store_id) == "ORD-000006"


@pytest.mark.asyncio
async def test_numbering_is_isolated_per_store(test_session):
    store_a, store_b = uuid.uuid4(), uuid.uuid4()
    test_session.add(_order(store_a, "ORD-000041"))
    await test_session.flush()

    repo = OrderRepository(test_session)
    assert await repo.get_next_order_number(store_a) == "ORD-000042"
    assert await repo.get_next_order_number(store_b) == "ORD-000001"

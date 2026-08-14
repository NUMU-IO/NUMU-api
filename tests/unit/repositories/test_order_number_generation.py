"""Order-number generation: random, unique, and volume-opaque.

History of this generator, because each scheme failed differently:

1. ``count(*) + 1`` reused a number whenever an order was deleted →
   real duplicate ``ORD-000033`` rows.
2. ``MAX + 1`` fixed reuse but published the merchant's order volume to
   anyone who placed two orders (the delta between two receipts a week
   apart is a competitor's sales report), and made the guest
   order-lookup form's number space walkable.
3. Now: six RANDOM digits (``ORD-######``, product decision 2026-08-14),
   collision-checked per store under the same advisory lock. Same shape
   as before so every parser (receipts, WhatsApp templates, the
   ``#ORD-…`` tracking normaliser) keeps working and legacy sequential
   numbers coexist.
"""

import re
import uuid
from datetime import UTC, datetime

import pytest

from src.core.entities.order import OrderStatus
from src.infrastructure.database.models import OrderModel
from src.infrastructure.repositories.order_repository import OrderRepository

_FORMAT = re.compile(r"^ORD-[1-9]\d{5}$")


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
async def test_number_has_the_canonical_shape(test_session):
    """ORD- prefix + exactly six digits, never a leading zero (the first
    digit is drawn from 1-9 so the width is stable)."""
    repo = OrderRepository(test_session)
    for _ in range(20):
        assert _FORMAT.match(await repo.get_next_order_number(uuid.uuid4()))


@pytest.mark.asyncio
async def test_numbers_do_not_reveal_order_count(test_session):
    """The volume-leak regression: consecutive draws must not be
    consecutive integers (sequential numbering let any customer read the
    merchant's sales volume off two receipts)."""
    store_id = uuid.uuid4()
    repo = OrderRepository(test_session)
    draws = [await repo.get_next_order_number(store_id) for _ in range(10)]
    values = [int(d.rsplit("-", 1)[-1]) for d in draws]
    deltas = {b - a for a, b in zip(values, values[1:])}
    # Ten sequential draws would produce deltas == {1}. Random draws
    # essentially never do.
    assert deltas != {1}


@pytest.mark.asyncio
async def test_existing_number_is_never_reissued(test_session):
    """Collision-retry regression (the duplicate ORD-000033 class of bug):
    a draw matching an existing row must be redrawn, per store."""
    store_id = uuid.uuid4()
    taken = {f"ORD-{n}" for n in range(100_000, 100_050)}
    for number in taken:
        test_session.add(_order(store_id, number))
    await test_session.flush()

    repo = OrderRepository(test_session)
    for _ in range(25):
        assert await repo.get_next_order_number(store_id) not in taken


@pytest.mark.asyncio
async def test_uniqueness_is_scoped_per_store(test_session):
    """A number taken in store A is still available to store B — numbers
    are unique within a store, not globally."""
    store_a, store_b = uuid.uuid4(), uuid.uuid4()
    test_session.add(_order(store_a, "ORD-424242"))
    await test_session.flush()

    repo = OrderRepository(test_session)
    # Store B is empty: any draw is fine, including 424242. Just assert
    # the generator works against a store whose numbers collide with
    # another store's.
    assert _FORMAT.match(await repo.get_next_order_number(store_b))


@pytest.mark.asyncio
async def test_coexists_with_legacy_sequential_numbers(test_session):
    """Stores carry ORD-000001-style history; the generator must not trip
    on them (they sit outside the 100000-999999 draw range entirely)."""
    store_id = uuid.uuid4()
    for number in ("ORD-000001", "ORD-000002", "ORD-000005"):
        test_session.add(_order(store_id, number))
    await test_session.flush()

    repo = OrderRepository(test_session)
    draw = await repo.get_next_order_number(store_id)
    assert _FORMAT.match(draw)
    assert draw not in {"ORD-000001", "ORD-000002", "ORD-000005"}

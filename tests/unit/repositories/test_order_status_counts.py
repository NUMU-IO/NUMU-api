"""``OrderRepository.count_by_status_for_store`` — the orders-list tab badges.

The hub's status tabs used to show a count only on "All" (and that count was
the *filtered* total). This GROUP BY powers a real count per tab, honouring
the same date / search / draft-exclusion filters the list applies.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.core.entities.order import OrderStatus
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.repositories.order_repository import OrderRepository


def _order(
    store_id, status: OrderStatus, *, days_ago: int = 1, number=None, notes=None
):
    created = datetime.now(UTC) - timedelta(days=days_ago)
    return OrderModel(
        id=uuid4(),
        store_id=store_id,
        tenant_id=store_id,
        customer_id=uuid4(),
        order_number=number or f"ORD-{uuid4().hex[:6]}",
        status=status,
        customer_notes=notes,
        line_items=[],
        shipping_address={
            "first_name": "T",
            "last_name": "U",
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
        created_at=created,
        updated_at=created,
        version=1,
    )


@pytest.mark.asyncio
async def test_counts_group_by_status_and_exclude_drafts(test_session):
    store_id = uuid4()
    other_store = uuid4()
    for s in (
        OrderStatus.PENDING,
        OrderStatus.PENDING,
        OrderStatus.SHIPPED,
        OrderStatus.DRAFT,
    ):
        test_session.add(_order(store_id, s))
    test_session.add(_order(other_store, OrderStatus.PENDING))
    await test_session.flush()

    repo = OrderRepository(test_session)
    counts = await repo.count_by_status_for_store(
        store_id, exclude_statuses=[OrderStatus.DRAFT]
    )

    assert counts == {"pending": 2, "shipped": 1}
    assert "draft" not in counts
    assert sum(counts.values()) == 3  # other store's order not counted


@pytest.mark.asyncio
async def test_counts_honour_date_window_and_search(test_session):
    store_id = uuid4()
    test_session.add(
        _order(store_id, OrderStatus.PENDING, days_ago=1, number="ORD-AAA111")
    )
    test_session.add(
        _order(store_id, OrderStatus.PENDING, days_ago=40, number="ORD-BBB222")
    )
    test_session.add(
        _order(
            store_id, OrderStatus.DELIVERED, days_ago=2, notes="please call aaa first"
        )
    )
    await test_session.flush()

    repo = OrderRepository(test_session)

    recent = await repo.count_by_status_for_store(
        store_id, date_from=datetime.now(UTC) - timedelta(days=7)
    )
    assert recent == {"pending": 1, "delivered": 1}

    # Search matches order number OR customer notes (same as the list's search).
    found = await repo.count_by_status_for_store(store_id, search="aaa")
    assert found == {"pending": 1, "delivered": 1}

    nothing = await repo.count_by_status_for_store(store_id, search="zzz")
    assert nothing == {}

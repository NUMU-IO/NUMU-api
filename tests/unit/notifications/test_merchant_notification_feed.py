"""Merchant notification feed — repository + emit service + event handlers.

Handlers open their own ``AsyncSessionLocal``; we patch that symbol to
the test engine's factory (same trick as the wallet handler tests).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import src.infrastructure.events.handlers.notification_feed_handler as handler_mod
from src.application.services.notification_feed import emit_notification
from src.core.entities.order import OrderStatus
from src.core.events.order_events import OrderCreatedEvent, OrderStatusChangedEvent
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.merchant_notification import (
    MerchantNotificationModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories.merchant_notification_repository import (
    MerchantNotificationRepository,
)


@pytest.fixture
def patched_sessions(test_engine, monkeypatch):
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(handler_mod, "AsyncSessionLocal", factory)
    return factory


async def _seed_store(session, *, settings=None) -> StoreModel:
    tenant = TenantModel(
        id=uuid4(),
        name="Tenant",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="free",
        lifecycle_state="active",
    )
    session.add(tenant)
    await session.flush()
    store = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=uuid4(),
        name="Store",
        slug=f"s-{uuid4().hex[:6]}",
        subdomain=f"s-{uuid4().hex[:6]}",
        status="active",
        default_currency="EGP",
        default_language="ar",
        settings=settings or {},
        theme_settings={},
        social_links={},
    )
    session.add(store)
    await session.commit()
    return store


def _row(
    store: StoreModel, *, category="orders", important=False, minutes_ago=0, read=False
):
    created = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return MerchantNotificationModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        category=category,
        kind=f"{category}.test",
        data={"n": minutes_ago},
        is_important=important,
        read_at=created if read else None,
        created_at=created,
    )


# ── repository ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_orders_newest_first_and_paginates_by_cursor(test_session):
    store = await _seed_store(test_session)
    for i in range(5):
        test_session.add(_row(store, minutes_ago=i))
    other = await _seed_store(test_session)
    test_session.add(_row(other, minutes_ago=0))
    await test_session.commit()

    repo = MerchantNotificationRepository(test_session)
    page1, cursor = await repo.list_for_store(store.id, limit=3)
    assert [r.data["n"] for r in page1] == [0, 1, 2]
    assert cursor is not None

    page2, cursor2 = await repo.list_for_store(store.id, limit=3, cursor=cursor)
    assert [r.data["n"] for r in page2] == [3, 4]
    assert cursor2 is None


@pytest.mark.asyncio
async def test_filters_category_important_unread(test_session):
    store = await _seed_store(test_session)
    test_session.add(_row(store, category="orders"))
    test_session.add(_row(store, category="payments", important=True))
    test_session.add(_row(store, category="payments", read=True, minutes_ago=5))
    await test_session.commit()

    repo = MerchantNotificationRepository(test_session)
    rows, _ = await repo.list_for_store(store.id, category="payments")
    assert len(rows) == 2
    rows, _ = await repo.list_for_store(store.id, important_only=True)
    assert len(rows) == 1 and rows[0].category == "payments"
    rows, _ = await repo.list_for_store(store.id, unread_only=True)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_unread_counts_and_mark_read(test_session):
    store = await _seed_store(test_session)
    a = _row(store, category="orders")
    b = _row(store, category="payments", important=True)
    c = _row(store, category="logistics", read=True)
    test_session.add_all([a, b, c])
    await test_session.commit()

    repo = MerchantNotificationRepository(test_session)
    counts = await repo.unread_counts(store.id)
    assert counts == {
        "total": 2,
        "important": 1,
        "by_category": {"orders": 1, "payments": 1},
    }

    assert await repo.mark_read(store.id, [a.id, c.id]) == 1
    await test_session.commit()
    assert (await repo.unread_counts(store.id))["total"] == 1

    assert await repo.mark_all_read(store.id, category="orders") == 0
    assert await repo.mark_all_read(store.id) == 1
    await test_session.commit()
    assert (await repo.unread_counts(store.id))["total"] == 0


@pytest.mark.asyncio
async def test_mark_read_is_store_scoped(test_session):
    store = await _seed_store(test_session)
    other = await _seed_store(test_session)
    foreign = _row(other)
    test_session.add(foreign)
    await test_session.commit()

    repo = MerchantNotificationRepository(test_session)
    assert await repo.mark_read(store.id, [foreign.id]) == 0
    assert await repo.mark_all_read(store.id) == 0


# ── emit service ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_dedupes_and_honours_muted_categories(test_session):
    store = await _seed_store(
        test_session,
        settings={"notification_center": {"muted_categories": ["logistics"]}},
    )
    kwargs = {
        "store_id": store.id,
        "category": "orders",
        "kind": "order.new",
        "data": {"order_number": "ORD-1"},
        "dedupe_key": "order.new:1",
    }
    assert await emit_notification(test_session, **kwargs) is True
    assert await emit_notification(test_session, **kwargs) is False
    assert (
        await emit_notification(
            test_session,
            store_id=store.id,
            category="logistics",
            kind="shipment.shipped",
        )
        is False
    )
    assert (
        await emit_notification(
            test_session, store_id=uuid4(), category="orders", kind="order.new"
        )
        is False
    )
    with pytest.raises(ValueError):
        await emit_notification(
            test_session, store_id=store.id, category="bogus", kind="x"
        )
    await test_session.commit()

    rows = (
        (
            await test_session.execute(
                select(MerchantNotificationModel).where(
                    MerchantNotificationModel.store_id == store.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].tenant_id == store.tenant_id


# ── handlers ──────────────────────────────────────────────────────────


async def _seed_order(session, store: StoreModel) -> OrderModel:
    order = OrderModel(
        id=uuid4(),
        store_id=store.id,
        tenant_id=store.tenant_id,
        customer_id=uuid4(),
        order_number="ORD-123456",
        status=OrderStatus.PENDING,
        line_items=[],
        shipping_address={
            "first_name": "Yahia",
            "last_name": "Sherif",
            "address_line1": "1 St",
            "city": "Cairo",
            "country": "EG",
            "phone": "+201111111111",
        },
        billing_address=None,
        subtotal=72900,
        shipping_cost=0,
        tax_amount=0,
        discount_amount=0,
        total=72900,
        currency="EGP",
        payment_method="cod",
        version=1,
    )
    session.add(order)
    await session.commit()
    return order


@pytest.mark.asyncio
async def test_order_created_handler_writes_feed_row(test_session, patched_sessions):
    store = await _seed_store(test_session)
    order = await _seed_order(test_session, store)

    event = OrderCreatedEvent(
        order_id=order.id,
        order_number=order.order_number,
        store_id=store.id,
        customer_id=order.customer_id,
        total=729.0,
        currency="EGP",
    )
    await handler_mod.handle_order_created_notification(event)
    await handler_mod.handle_order_created_notification(event)  # replay

    async with patched_sessions() as s:
        rows = (
            (
                await s.execute(
                    select(MerchantNotificationModel).where(
                        MerchantNotificationModel.store_id == store.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.category == "orders"
    assert row.kind == "order.new"
    assert row.link == f"/orders/{order.id}"
    assert row.data["customer_name"] == "Yahia Sherif"
    assert row.data["total_cents"] == 72900
    assert row.data["payment_method"] == "cod"
    assert row.is_important is False


@pytest.mark.asyncio
async def test_status_handler_maps_cancelled_to_important(
    test_session, patched_sessions
):
    store = await _seed_store(test_session)
    order = await _seed_order(test_session, store)

    base = {
        "order_id": order.id,
        "order_number": order.order_number,
        "store_id": store.id,
        "store_name": store.name,
        "customer_id": order.customer_id,
        "customer_name": "Yahia Sherif",
        "previous_status": "pending",
    }
    await handler_mod.handle_order_status_notification(
        OrderStatusChangedEvent(**base, new_status="confirmed")
    )
    await handler_mod.handle_order_status_notification(
        OrderStatusChangedEvent(**base, new_status="cancelled", reason="no stock")
    )
    await handler_mod.handle_order_status_notification(
        OrderStatusChangedEvent(
            **base, new_status="shipped", carrier="bosta", tracking_number="BX1"
        )
    )

    async with patched_sessions() as s:
        rows = (
            (
                await s.execute(
                    select(MerchantNotificationModel)
                    .where(MerchantNotificationModel.store_id == store.id)
                    .order_by(MerchantNotificationModel.kind)
                )
            )
            .scalars()
            .all()
        )
    assert [(r.category, r.kind, r.is_important) for r in rows] == [
        ("orders", "order.cancelled", True),
        ("logistics", "shipment.shipped", False),
    ]
    assert rows[0].data["reason"] == "no stock"
    assert rows[1].data["tracking_number"] == "BX1"

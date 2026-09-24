"""Bosta and J&T callbacks announce order changes through the dashboard's
post-transition path: one ``OrderStatusChangedEvent`` per real change,
nothing on a replay, no downgrade, unknown statuses ignored."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import pytest

from src.api.v1.routes.webhooks import bosta, jt
from src.application.services import shipment_status_sync
from src.application.use_cases.orders.update_order_status import (
    UpdateOrderStatusUseCase,
)
from src.core.entities.order import Order, OrderShippingAddress, OrderStatus
from src.core.entities.shipment import Shipment, ShipmentStatus
from src.core.events.order_events import OrderStatusChangedEvent
from src.infrastructure.external_services.jt.shipping_service import md5_base64

STORE = uuid4()
TENANT = uuid4()
TRACKING = "TRK-1"
PRIVATE_KEY = "pk-test"


class _Session:
    async def commit(self):
        pass

    async def rollback(self):
        pass


class _Bus:
    def __init__(self):
        self.events: list[OrderStatusChangedEvent] = []

    def publish(self, event):
        self.events.append(event)


class _Repo:
    session = None

    def __init__(self, item):
        self.item = item

    async def get_by_tracking_number_for_update(self, tracking_number):
        return self.item if self.item and tracking_number == TRACKING else None

    async def get_by_id(self, _id):
        return self.item

    async def update(self, item):
        return item


class _Stores:
    def __init__(self, session=None):
        pass

    async def get_by_id(self, _id):
        return SimpleNamespace(
            id=STORE,
            tenant_id=TENANT,
            name="Store",
            owner_id=uuid4(),
            default_language="ar",
            settings={},
        )


def _order(status: OrderStatus) -> Order:
    return Order(
        store_id=STORE,
        tenant_id=TENANT,
        customer_id=uuid4(),
        order_number="ORD-1",
        status=status,
        payment_method="cod",
        total=30000,
        tracking_number=TRACKING,
        shipping_address=OrderShippingAddress(
            first_name="A", last_name="B", address_line1="x", city="Cairo", country="EG"
        ),
    )


def _shipment(status=ShipmentStatus.IN_TRANSIT) -> Shipment:
    return Shipment(
        store_id=STORE,
        tenant_id=TENANT,
        order_id=uuid4(),
        carrier="bosta",
        tracking_number=TRACKING,
        status=status,
        cod_amount=30000,
    )


@pytest.fixture
def world(monkeypatch):
    bus = _Bus()
    state = SimpleNamespace(order=None, shipment=None, bus=bus)

    def orders(_session):
        return _Repo(state.order)

    def shipments(_session):
        return _Repo(state.shipment)

    def use_case(_session):
        return UpdateOrderStatusUseCase(
            order_repository=_Repo(state.order),
            store_repository=_Stores(),
            event_bus=bus,
        )

    async def noop(*args, **kwargs):
        return None

    for module in (bosta, jt):
        monkeypatch.setattr(module, "OrderRepository", orders)
        monkeypatch.setattr(module, "ShipmentRepository", shipments)
        monkeypatch.setattr(module, "narrow_to_tenant", noop)
        monkeypatch.setattr(module, "emit_order_delivered", noop)
    monkeypatch.setattr(jt, "StoreRepository", _Stores)
    monkeypatch.setattr(jt, "_enqueue_purchase_events", noop)

    async def creds(*args, **kwargs):
        return {"private_key": PRIVATE_KEY}

    monkeypatch.setattr(jt, "load_credentials", creds)
    monkeypatch.setattr(
        "src.application.services.stock_service.try_restock_order", noop
    )
    monkeypatch.setattr(shipment_status_sync, "_status_use_case", use_case)
    return state


def _request(raw: bytes, headers: dict | None = None):
    async def body():
        return raw

    return SimpleNamespace(body=body, headers=headers or {})


async def _bosta(state: str):
    raw = json.dumps({
        "delivery": {"trackingNumber": TRACKING, "state": {"value": state}}
    }).encode()
    await bosta.bosta_callback(_request(raw), _Session(), None)


async def _jt(scan: str):
    content = json.dumps({
        "billCode": TRACKING,
        "details": [{"scanTime": "2026-09-24 10:00:00", "scanType": scan}],
    })
    raw = urlencode({"bizContent": content}).encode()
    digest = md5_base64(content + PRIVATE_KEY)
    return await jt.jt_callback(_request(raw, {"digest": digest}), _Session())


def _statuses(world):
    return [(e.previous_status, e.new_status) for e in world.bus.events]


# ─── Bosta ─────────────────────────────────────────────────────────


async def test_bosta_delivered_announces_once_and_replay_is_silent(world):
    world.order = _order(OrderStatus.SHIPPED)
    world.shipment = _shipment()
    await _bosta("DELIVERED")
    assert _statuses(world) == [("shipped", "delivered")]

    await _bosta("DELIVERED")
    assert _statuses(world) == [("shipped", "delivered")], "a replay re-sends nothing"


async def test_bosta_pickup_ships_a_confirmed_order_in_one_event(world):
    world.order = _order(OrderStatus.CONFIRMED)
    world.shipment = _shipment(ShipmentStatus.CREATED)
    await _bosta("PICKED_UP")
    assert world.order.status == OrderStatus.SHIPPED
    assert _statuses(world) == [("confirmed", "shipped")]


async def test_bosta_never_downgrades_a_delivered_order(world):
    world.order = _order(OrderStatus.DELIVERED)
    world.shipment = _shipment(ShipmentStatus.DELIVERED)
    await _bosta("PICKED_UP")
    assert world.order.status == OrderStatus.DELIVERED
    assert world.bus.events == []


async def test_bosta_unknown_state_is_ignored(world):
    world.order = _order(OrderStatus.SHIPPED)
    world.shipment = _shipment()
    await _bosta("SOMETHING_NEW")
    assert world.order.status == OrderStatus.SHIPPED
    assert world.bus.events == []


# ─── J&T ───────────────────────────────────────────────────────────


async def test_jt_delivered_announces_once_and_replay_is_silent(world):
    world.order = _order(OrderStatus.SHIPPED)
    world.shipment = _shipment()
    await _jt("Signing scan")
    assert _statuses(world) == [("shipped", "delivered")]

    await _jt("Signing scan")
    assert _statuses(world) == [("shipped", "delivered")]


async def test_jt_pickup_ships_once(world):
    world.order = _order(OrderStatus.PROCESSING)
    world.shipment = _shipment(ShipmentStatus.CREATED)
    await _jt("Pickup scan")
    assert _statuses(world) == [("processing", "shipped")]


async def test_jt_never_downgrades_a_delivered_order(world):
    world.order = _order(OrderStatus.DELIVERED)
    world.shipment = _shipment(ShipmentStatus.DELIVERED)
    await _jt("Pickup scan")
    assert world.order.status == OrderStatus.DELIVERED
    assert world.bus.events == []


async def test_jt_unknown_scan_is_ignored(world):
    world.order = _order(OrderStatus.SHIPPED)
    world.shipment = _shipment()
    await _jt("Brand new scan")
    assert world.bus.events == []


# ─── Shared path ───────────────────────────────────────────────────


async def test_same_status_is_never_announced_twice():
    bus = _Bus()
    use_case = UpdateOrderStatusUseCase(
        order_repository=_Repo(None), store_repository=_Stores(), event_bus=bus
    )
    order = _order(OrderStatus.DELIVERED)
    await use_case.after_status_change(
        order, "delivered", OrderStatus.DELIVERED, await _Stores().get_by_id(STORE)
    )
    assert bus.events == []

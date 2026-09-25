"""The Trust Network "hold" action: a high-risk COD order is allowed but held
for review, is not booked with a courier, and is released or cancelled by
the review routes."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.v1.routes.stores import cod
from src.application.services.cod_trust_service import check_customer_trust
from src.core.entities.order import OrderStatus
from src.infrastructure.events.handlers.shipment_handler import books_on_creation


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    class _S:
        platform_secret_salt = "test-salt"

    monkeypatch.setattr(
        "src.application.services.network_reputation_service.get_settings", lambda: _S()
    )

    async def risky(*, phone_hash, total_cents=None):
        return (90, "high", "serial_abuser")

    monkeypatch.setattr(
        "src.application.services.trust_network_storefront.fetch_network_intelligence",
        risky,
    )


def _settings(action: str) -> dict:
    return {
        "cod_trust": {
            "enabled": True,
            "threshold": 70,
            "min_confidence": "medium",
            "action": action,
        }
    }


async def test_hold_lets_a_risky_order_through_but_flags_it():
    decision = await check_customer_trust(
        phone="+201001234567", store_settings=_settings("hold"), network_repo=None
    )
    assert decision.allowed is True
    assert decision.hold is True
    assert decision.reason == "held_high_risk"


async def test_block_still_blocks():
    decision = await check_customer_trust(
        phone="+201001234567", store_settings=_settings("block"), network_repo=None
    )
    assert decision.allowed is False and decision.hold is False


def test_a_held_order_is_not_booked_on_creation():
    assert books_on_creation({}, "cod", "pending") is True
    assert books_on_creation({}, "cod", "pending", "held") is False
    assert books_on_creation({}, "cod", "pending", "approved") is True


# ─── Review routes (fakes: no database) ───────────────────────────


class _Order(SimpleNamespace):
    def confirm(self):
        assert self.status == OrderStatus.PENDING
        self.status = OrderStatus.CONFIRMED

    def cancel(self, reason):
        self.status = OrderStatus.CANCELLED


class _Repo:
    def __init__(self, order):
        self.order = order

    async def get_by_id(self, order_id):
        return self.order if self.order.id == order_id else None

    async def update(self, order):
        return order


class _Db:
    async def commit(self):
        pass


@pytest.fixture
def world(monkeypatch):
    store = SimpleNamespace(id=uuid4(), name="S", default_language="ar")
    order = _Order(
        id=uuid4(),
        store_id=store.id,
        order_number="ORD-1",
        customer_id=uuid4(),
        status=OrderStatus.PENDING,
        cod_review_status="held",
        cod_reviewed_at=None,
    )
    monkeypatch.setattr(
        "src.infrastructure.repositories.order_repository.OrderRepository",
        lambda db: _Repo(order),
    )
    published: list = []

    async def publish(db, store, order, previous, new, reason):
        published.append((previous, new, reason))

    monkeypatch.setattr(cod, "_publish_status", publish)
    restocked: list = []

    async def restock(db, order, reason):
        restocked.append(reason)

    monkeypatch.setattr(
        "src.application.services.stock_service.try_restock_order", restock
    )
    return SimpleNamespace(
        store=store, order=order, published=published, restocked=restocked
    )


async def test_approving_confirms_and_books(world):
    out = (
        await cod.approve_held_order(world.order.id, store=world.store, db=_Db())
    ).data
    assert out["status"] == "confirmed"
    assert world.order.cod_review_status == "approved"
    assert world.published == [("pending", "confirmed", "cod_review_approved")]


async def test_rejecting_cancels_and_restocks(world):
    await cod.reject_held_order(
        world.order.id, cod.RejectRequest(), store=world.store, db=_Db()
    )
    assert world.order.status == OrderStatus.CANCELLED
    assert world.order.cod_review_status == "rejected"
    assert world.restocked == ["cod_review_rejected"]
    assert world.published[0][1] == "cancelled"


async def test_an_order_that_is_not_held_is_refused(world):
    world.order.cod_review_status = None
    with pytest.raises(HTTPException) as exc:
        await cod.approve_held_order(world.order.id, store=world.store, db=_Db())
    assert exc.value.status_code == 409


async def test_another_stores_order_is_not_found(world):
    world.order.store_id = uuid4()
    with pytest.raises(HTTPException) as exc:
        await cod.approve_held_order(world.order.id, store=world.store, db=_Db())
    assert exc.value.status_code == 404

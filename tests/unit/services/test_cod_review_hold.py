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


# ─── Deposit request ──────────────────────────────────────────────


def _store(world):
    world.store.store_url = "https://shop.numueg.app"
    return world.store


async def test_a_percent_deposit_moves_the_order_to_pending_deposit(world):
    world.order.payment_method = "cod"
    world.order.total = 150_000
    out = (
        await cod.request_deposit(
            world.order.id,
            cod.DepositRequest(percent=10),
            store=_store(world),
            db=_Db(),
        )
    ).data
    assert out.deposit_cents == 15_000 and out.balance_due_cents == 135_000
    assert out.pay_url == f"https://shop.numueg.app/pay/{world.order.id}"
    assert world.order.status == OrderStatus.PENDING_DEPOSIT
    assert world.order.deposit_required_cents == 15_000
    assert world.order.cod_review_status == "deposit"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (cod.DepositRequest(), 422),
        (cod.DepositRequest(amount_cents=500, percent=10), 422),
        (cod.DepositRequest(amount_cents=150_000), 422),
    ],
)
async def test_a_bad_deposit_is_refused(world, body, code):
    world.order.payment_method = "cod"
    world.order.total = 150_000
    with pytest.raises(HTTPException) as exc:
        await cod.request_deposit(world.order.id, body, store=_store(world), db=_Db())
    assert exc.value.status_code == code


async def test_a_prepaid_order_takes_no_deposit(world):
    world.order.payment_method = "paymob"
    world.order.total = 150_000
    with pytest.raises(HTTPException) as exc:
        await cod.request_deposit(
            world.order.id,
            cod.DepositRequest(percent=10),
            store=_store(world),
            db=_Db(),
        )
    assert exc.value.status_code == 409


def test_the_pay_page_charges_the_deposit_while_one_is_due():
    from src.api.v1.routes.storefront.pay import _amount_due

    waiting = SimpleNamespace(
        status=OrderStatus.PENDING_DEPOSIT, deposit_required_cents=15_000, total=150_000
    )
    open_order = SimpleNamespace(
        status=OrderStatus.PENDING, deposit_required_cents=None, total=150_000
    )
    assert _amount_due(waiting) == 15_000
    assert _amount_due(open_order) == 150_000


# ─── COD Shield gate ──────────────────────────────────────────────


class _Session:
    """scalar() answers in order: the gate config, then the install lookup."""

    def __init__(self, *answers):
        self.answers = list(answers)

    async def scalar(self, _stmt):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_the_gate_is_open_until_it_is_required():
    from src.application.services.cod_shield import cod_shield_allows

    assert await cod_shield_allows(_Session(None), uuid4()) is True
    assert await cod_shield_allows(_Session({"required": False}), uuid4()) is True


async def test_a_required_gate_needs_the_app_installed():
    from src.application.services.cod_shield import cod_shield_allows

    required = {"required": True, "app_slug": "cod-shield"}
    assert await cod_shield_allows(_Session(required, None), uuid4()) is False
    assert await cod_shield_allows(_Session(required, uuid4()), uuid4()) is True


async def test_the_gate_fails_open():
    from src.application.services.cod_shield import cod_shield_allows

    assert await cod_shield_allows(_Session(RuntimeError("db down")), uuid4()) is True
    assert await cod_shield_allows(None, uuid4()) is True

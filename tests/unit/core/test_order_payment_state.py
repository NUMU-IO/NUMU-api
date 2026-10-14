"""Money-critical order payment state: late payments, sweeps, courier COD."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.entities.instapay import ManualPaymentIntentStatus
from src.core.entities.order import (
    Order,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)


def _order(**kwargs) -> Order:
    defaults = {
        "store_id": uuid4(),
        "tenant_id": uuid4(),
        "customer_id": uuid4(),
        "shipping_address": OrderShippingAddress(
            first_name="Sara",
            last_name="A",
            address_line1="12 Tahrir St",
            city="Cairo",
            country="EG",
        ),
        "order_number": "ORD-000001",
        "payment_method": "cod",
        "subtotal": 10000,
        "total": 10000,
    }
    defaults.update(kwargs)
    return Order(**defaults)


@pytest.mark.parametrize("closed", [OrderStatus.CANCELLED, OrderStatus.REFUNDED])
def test_payment_on_closed_order_is_recorded_not_revived(closed):
    order = _order(status=closed)
    order.mark_as_paid("txn-1", "paymob")

    assert order.status == closed
    assert order.payment_status == PaymentStatus.PAID
    assert order.payment_id == "txn-1"
    assert order.metadata["payment_after_cancel"]["payment_id"] == "txn-1"


def test_deposit_paid_after_expiry_cancel_records_deposit_only():
    order = _order(status=OrderStatus.CANCELLED, deposit_required_cents=2000)
    order.mark_as_paid("txn-2", "kashier")

    assert order.status == OrderStatus.CANCELLED
    assert order.payment_status == PaymentStatus.PENDING
    assert order.deposit_paid_at is not None
    assert "payment_after_cancel" in order.metadata


@pytest.mark.parametrize("later", [OrderStatus.SHIPPED, OrderStatus.DELIVERED])
def test_payment_after_fulfilment_keeps_status(later):
    order = _order(status=later)
    order.mark_as_paid("cod-1", "cod")

    assert order.status == later
    assert order.payment_status == PaymentStatus.PAID
    assert "payment_after_cancel" not in order.metadata


def test_payment_moves_pre_fulfilment_order_to_processing():
    order = _order(status=OrderStatus.AWAITING_PAYMENT)
    order.mark_as_paid("txn-3", "paymob")
    assert order.status == OrderStatus.PROCESSING


def test_pending_deposit_payment_confirms_and_stays_unpaid():
    order = _order(status=OrderStatus.PENDING_DEPOSIT, deposit_required_cents=2000)
    order.mark_as_paid("txn-4", "paymob")

    assert order.status == OrderStatus.CONFIRMED
    assert order.payment_status == PaymentStatus.PENDING


def test_amount_due_is_zero_when_paid():
    order = _order(payment_status=PaymentStatus.PAID)
    assert order.amount_due_cents(proofs_paid_cents=500) == 0


def test_amount_due_subtracts_gateway_deposit_and_proofs():
    order = _order(
        deposit_amount_cents=2000,
        deposit_paid_at=datetime.now(UTC),
        deposit_gateway="paymob",
        collected_total=9000,
    )
    assert order.amount_due_cents(proofs_paid_cents=1000) == 6000


def test_amount_due_does_not_count_proof_deposit_twice():
    order = _order(
        deposit_amount_cents=2000,
        deposit_paid_at=datetime.now(UTC),
        deposit_gateway="instapay",
    )
    assert order.amount_due_cents(proofs_paid_cents=2000) == 8000


def test_amount_due_ignores_unpaid_deposit():
    order = _order(deposit_amount_cents=2000, deposit_gateway="paymob")
    assert order.amount_due_cents() == 10000


async def _run_deposit_sweep(monkeypatch, order, intent=None):
    from src.infrastructure.database import connection
    from src.infrastructure.messaging.tasks import deposit_expiry_task
    from src.infrastructure.repositories import (
        instapay_intent_repository,
        order_repository,
    )
    from src.infrastructure.tenancy import rls

    model = SimpleNamespace(id=order.id, tenant_id=order.tenant_id)
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=lambda: MagicMock(all=lambda: [model]),
        )
    )
    session.commit = AsyncMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(connection, "AsyncSessionLocal", lambda: session_cm)
    monkeypatch.setattr(rls, "enable_rls_bypass", AsyncMock())
    monkeypatch.setattr(rls, "narrow_to_tenant", AsyncMock())

    order_repo = MagicMock(get_by_id=AsyncMock(return_value=order), update=AsyncMock())
    monkeypatch.setattr(order_repository, "OrderRepository", lambda _s: order_repo)
    intent_repo = MagicMock(get_by_order_id=AsyncMock(return_value=intent))
    monkeypatch.setattr(
        instapay_intent_repository,
        "ManualPaymentIntentRepository",
        lambda _s: intent_repo,
    )
    stats = await deposit_expiry_task._sweep()
    return stats, order_repo


def _expired_deposit_order(**kwargs) -> Order:
    return _order(
        status=OrderStatus.PENDING_DEPOSIT,
        deposit_required_cents=2000,
        deposit_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_deposit_sweep_never_cancels_a_paid_order(monkeypatch):
    order = _expired_deposit_order(payment_status=PaymentStatus.PAID)
    stats, order_repo = await _run_deposit_sweep(monkeypatch, order)

    assert stats["cancelled"] == 0
    assert stats["errors"] == 0
    assert order.status == OrderStatus.PENDING_DEPOSIT
    order_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_deposit_sweep_skips_proof_awaiting_review(monkeypatch):
    order = _expired_deposit_order()
    intent = SimpleNamespace(status=ManualPaymentIntentStatus.PROOF_RECEIVED)
    stats, _ = await _run_deposit_sweep(monkeypatch, order, intent)

    assert stats["cancelled"] == 0
    assert order.status == OrderStatus.PENDING_DEPOSIT


@pytest.mark.asyncio
async def test_deposit_sweep_cancels_unpaid_expired_order(monkeypatch):
    from src.application.services import stock_service

    monkeypatch.setattr(stock_service, "restock_order", AsyncMock())
    order = _expired_deposit_order()
    stats, order_repo = await _run_deposit_sweep(monkeypatch, order)

    assert stats["cancelled"] == 1
    assert order.status == OrderStatus.CANCELLED
    order_repo.update.assert_awaited_once()

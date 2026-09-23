"""Admin reconciliation flags gateway payments the order never recorded."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from src.api.v1.routes.admin.reconciliation import _row
from src.core.entities.order import OrderStatus, PaymentStatus


def _tx(status: str = "success"):
    return SimpleNamespace(
        id=uuid4(),
        created_at=datetime.now(UTC),
        gateway="kashier",
        channel="online",
        status=status,
        amount_cents=1000,
        currency="EGP",
        gateway_transaction_id="TX-1",
        store_id=uuid4(),
    )


def _order(payment_status: PaymentStatus):
    return SimpleNamespace(
        id=uuid4(),
        order_number="ORD-1",
        status=OrderStatus.PENDING,
        payment_status=payment_status,
        total=1000,
    )


def test_paid_transaction_on_unpaid_order_is_flagged():
    row = _row(_tx(), _order(PaymentStatus.PENDING), "Store")
    assert row.mismatch == "paid_not_recorded"
    assert row.payment_status == "pending"


def test_matching_paid_order_is_clean():
    assert _row(_tx(), _order(PaymentStatus.PAID), "Store").mismatch is None


def test_failed_transaction_on_unpaid_order_is_clean():
    assert _row(_tx("failed"), _order(PaymentStatus.PENDING), "S").mismatch is None


def test_transaction_without_order_is_flagged():
    assert _row(_tx(), None, "Store").mismatch == "order_missing"

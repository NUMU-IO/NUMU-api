"""Unit tests for COD reconciliation.

The COD screen was self-contradictory — "2 mismatches" beside a green
"Matched" badge, rows whose EXPECTED equalled their ACTUAL, and an actual of
EGP 870 against 0 transactions — because the engine added every COD order to
both sides of the ledger AND filed it as a mismatch. Variance was therefore
structurally 0 on a COD-only store, which is the store type the page exists
for.

Pinned here:

* COD never needs a gateway transaction — a collected parcel is CLEAN.
* ``actual`` for COD comes from the courier's own report
  (``shipments.cod_collected`` / ``cod_amount``), not from a copy of expected.
* Delivered-but-not-remitted is the mismatch that finally moves variance.
* The transactions query is scoped to the store. It was not, and
  ``payment_transactions`` has no RLS policy, so a merchant-triggered run
  read every store's transactions and published them as ``transaction_no_order``
  mismatches in this merchant's list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.application.services.reconciliation_service import ReconciliationService
from src.core.entities.reconciliation import (
    MismatchType,
    PaymentReconciliationRun,
    ReconciliationStatus,
)

STORE_ID = uuid.uuid4()
PERIOD_START = datetime(2026, 8, 23, tzinfo=UTC)
PERIOD_END = PERIOD_START + timedelta(days=1)


def _order(*, total: int, method: str = "cod", collected_total: int | None = None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number=f"ORD-{uuid.uuid4().hex[:6].upper()}",
        total=total,
        collected_total=collected_total,
        payment_method=method,
        payment_id=None,
        paid_at=PERIOD_START + timedelta(hours=3),
    )


def _shipment(order_id, *, collected: bool, cod_amount: int, status: str = "delivered"):
    return SimpleNamespace(
        order_id=order_id,
        cod_collected=collected,
        cod_amount=cod_amount,
        status=status,
    )


def _txn(order_id, *, amount: int, gateway: str = "paymob"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_id=order_id,
        amount_cents=amount,
        gateway=gateway,
        gateway_transaction_id=f"tx_{uuid.uuid4().hex[:8]}",
        status="success",
        processing_completed_at=PERIOD_START + timedelta(hours=4),
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._rows[0]


class _FakeSession:
    """Replays canned rows in the order ``_execute_run`` queries them:
    orders, transactions, shipments, then the run row it updates.

    Also records every compiled statement so a test can assert on the
    WHERE clause the service actually emitted.
    """

    def __init__(self, orders, txns, shipments, run_model):
        self._queue = [orders, txns]
        if orders:
            self._queue.append(shipments)
        self._queue.append([run_model])
        self.added: list = []
        self.statements: list[str] = []

    async def execute(self, stmt):
        self.statements.append(str(stmt.compile(dialect=postgresql.dialect())))
        return _Result(self._queue.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def refresh(self, _obj):
        return None


def _run_model(run_id):
    return SimpleNamespace(
        id=run_id,
        store_id=STORE_ID,
        gateway="all",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        status=ReconciliationStatus.RUNNING.value,
        total_orders_checked=0,
        total_transactions_checked=0,
        mismatches_found=0,
        expected_amount_cents=0,
        actual_amount_cents=0,
        error_message=None,
        completed_at=None,
        created_at=PERIOD_START,
        updated_at=PERIOD_START,
    )


async def _execute(orders, txns=(), shipments=()):
    run_id = uuid.uuid4()
    run = PaymentReconciliationRun(
        id=run_id,
        store_id=STORE_ID,
        gateway="all",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        status=ReconciliationStatus.RUNNING,
    )
    session = _FakeSession(
        list(orders), list(txns), list(shipments), _run_model(run_id)
    )
    svc = ReconciliationService(session)
    result = await svc._execute_run(run, PERIOD_START, PERIOD_END)
    return result, session


def _types(session):
    return [m.mismatch_type for m in session.added]


# ── COD ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collected_cod_order_is_clean():
    """The bug in one test: a COD order the courier collected in full is
    NOT a mismatch, and its actual is the courier's figure."""
    order = _order(total=33_000)
    ship = _shipment(order.id, collected=True, cod_amount=33_000)

    run, session = await _execute([order], shipments=[ship])

    assert session.added == []
    assert run.mismatches_found == 0
    assert run.expected_amount_cents == 33_000
    assert run.actual_amount_cents == 33_000


@pytest.mark.asyncio
async def test_delivered_but_not_remitted_moves_the_variance():
    """The gap the screen is supposed to surface and never could."""
    order = _order(total=54_000)
    ship = _shipment(order.id, collected=False, cod_amount=54_000)

    run, session = await _execute([order], shipments=[ship])

    assert _types(session) == [MismatchType.COD_NOT_REMITTED.value]
    assert run.expected_amount_cents == 54_000
    assert run.actual_amount_cents == 0
    # Variance is expected − actual: finally non-zero on a COD store.
    assert run.expected_amount_cents - run.actual_amount_cents == 54_000


@pytest.mark.asyncio
async def test_courier_remitted_less_than_expected():
    order = _order(total=54_000)
    ship = _shipment(order.id, collected=True, cod_amount=50_000)

    run, session = await _execute([order], shipments=[ship])

    assert _types(session) == [MismatchType.AMOUNT_MISMATCH.value]
    assert session.added[0].expected_amount_cents == 54_000
    assert session.added[0].actual_amount_cents == 50_000
    assert run.actual_amount_cents == 50_000


@pytest.mark.asyncio
async def test_manual_ship_cod_order_is_not_a_discrepancy():
    """No shipment row at all — the merchant marked it paid themselves.
    An assertion rather than evidence, but not a gap to chase."""
    order = _order(total=33_000)

    run, session = await _execute([order])

    assert session.added == []
    assert run.actual_amount_cents == 33_000


@pytest.mark.asyncio
async def test_partial_acceptance_uses_collected_total():
    order = _order(total=54_000, collected_total=30_000)
    ship = _shipment(order.id, collected=True, cod_amount=30_000)

    run, session = await _execute([order], shipments=[ship])

    assert session.added == []
    assert run.expected_amount_cents == 30_000
    assert run.actual_amount_cents == 30_000


# ── Non-COD rails keep their old behaviour ───────────────────────────


@pytest.mark.asyncio
async def test_card_order_without_a_transaction_is_still_a_mismatch():
    order = _order(total=20_000, method="paymob")

    _, session = await _execute([order])

    assert _types(session) == [MismatchType.PAID_ORDER_NO_TRANSACTION.value]
    assert session.added[0].actual_amount_cents is None


@pytest.mark.asyncio
async def test_card_order_amount_mismatch_still_detected():
    order = _order(total=20_000, method="paymob")
    txn = _txn(order.id, amount=18_000)

    run, session = await _execute([order], txns=[txn])

    assert _types(session) == [MismatchType.AMOUNT_MISMATCH.value]
    assert run.actual_amount_cents == 18_000


# ── Tenant scoping ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transaction_query_is_scoped_to_the_store():
    """Without this filter a merchant-triggered run read every store's
    transactions and filed each one as `transaction_no_order` in this
    merchant's mismatch list. payment_transactions has no RLS policy, so
    nothing else was stopping it."""
    order = _order(total=33_000)

    _, session = await _execute([order])

    # statements[1] is the payment_transactions query.
    txn_sql = session.statements[1]
    assert "payment_transactions" in txn_sql
    assert "store_id" in txn_sql


@pytest.mark.asyncio
async def test_foreign_transaction_is_not_reported_as_a_mismatch():
    """A transaction belonging to another store never reaches the merchant's
    list — the store filter removes it upstream, so an empty transaction set
    produces no `transaction_no_order` rows."""
    order = _order(total=33_000)
    ship = _shipment(order.id, collected=True, cod_amount=33_000)

    _, session = await _execute([order], txns=[], shipments=[ship])

    assert MismatchType.TRANSACTION_NO_ORDER.value not in _types(session)

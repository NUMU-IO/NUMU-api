"""Approving an InstaPay proof for a COD deposit collects the deposit only.

The order stays unpaid (the balance is collected on delivery), the proof
carries the deposit amount so the order page counts it as paid, and no
OrderPaidEvent fires — that event invoices and charges commission on the
full order total.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.application.use_cases.payments.review_payment_proof import (
    ReviewDecision,
    ReviewPaymentProofUseCase,
)
from src.core.entities.instapay import (
    ManualPaymentIntent,
    ManualPaymentIntentStatus,
    PaymentProof,
)
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)


def _order(status: OrderStatus) -> Order:
    return Order(
        id=uuid4(),
        store_id=uuid4(),
        tenant_id=uuid4(),
        customer_id=uuid4(),
        order_number="ORD-759998",
        line_items=[
            OrderLineItem(
                product_id=uuid4(),
                product_name="Book",
                quantity=1,
                unit_price=84_000,
                total_price=84_000,
            )
        ],
        shipping_address=OrderShippingAddress(
            first_name="Test",
            last_name="Customer",
            address_line1="10 Tahrir Square",
            city="Cairo",
            country="EG",
        ),
        status=status,
        payment_status=PaymentStatus.PENDING,
        subtotal=84_000,
        total=84_000,
        currency="EGP",
        payment_method="cod",
    )


def _intent(order: Order, amount_cents: int) -> ManualPaymentIntent:
    return ManualPaymentIntent(
        id=uuid4(),
        tenant_id=order.tenant_id,
        store_id=order.store_id,
        order_id=order.id,
        reference_code="NU-NJYSYJ",
        display_destination="shop@instapay",
        amount_cents=amount_cents,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        qr_payload="instapay://pay?...",
        status=ManualPaymentIntentStatus.PROOF_RECEIVED,
    )


async def _approve(order: Order, intent: ManualPaymentIntent, declared=None):
    proof = PaymentProof.new(
        tenant_id=order.tenant_id,
        store_id=order.store_id,
        order_id=order.id,
        proof_image_key="k",
        proof_image_hash=b"h" * 32,
        transaction_ref="01509939127",
        declared_amount_cents=declared,
    )
    proof_repo = MagicMock()
    proof_repo.get_by_id = AsyncMock(return_value=proof)
    proof_repo.update = AsyncMock(side_effect=lambda p: p)
    order_repo = MagicMock()
    order_repo.get_by_id = AsyncMock(return_value=order)
    order_repo.update = AsyncMock()
    intent_repo = MagicMock()
    intent_repo.get_by_order_id = AsyncMock(return_value=intent)
    intent_repo.update = AsyncMock()
    session = MagicMock()
    session.flush = AsyncMock()
    bus = MagicMock()
    with (
        patch("src.infrastructure.events.setup.get_event_bus", return_value=bus),
        patch(
            "src.application.services.funnel_emit_service.emit_order_completed",
            new=AsyncMock(),
        ) as funnel,
    ):
        await ReviewPaymentProofUseCase(
            session=session,
            order_repo=order_repo,
            intent_repo=intent_repo,
            proof_repo=proof_repo,
        ).execute(
            proof_id=proof.id,
            reviewer_user_id=uuid4(),
            decision=ReviewDecision.APPROVE,
        )
    published = {type(c.args[0]).__name__ for c in bus.publish.call_args_list}
    txn = session.add.call_args.args[0]
    return proof, published, txn, funnel


@pytest.mark.asyncio
async def test_deposit_approval_records_deposit_and_leaves_order_unpaid():
    order = _order(OrderStatus.PENDING_DEPOSIT)
    proof, published, txn, funnel = await _approve(order, _intent(order, 42_000))

    assert order.payment_status == PaymentStatus.PENDING
    assert order.status == OrderStatus.CONFIRMED
    assert order.deposit_paid_at is not None
    assert order.deposit_amount_cents == 42_000
    assert proof.declared_amount_cents == 42_000
    assert txn.amount_cents == 42_000
    assert "OrderPaidEvent" not in published
    assert "PaymentProofApprovedEvent" in published
    funnel.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_payment_approval_still_marks_order_paid():
    order = _order(OrderStatus.PENDING)
    proof, published, txn, funnel = await _approve(order, _intent(order, 84_000))

    assert order.payment_status == PaymentStatus.PAID
    assert proof.declared_amount_cents == 84_000
    assert txn.amount_cents == 84_000
    assert "OrderPaidEvent" in published
    funnel.assert_awaited_once()


@pytest.mark.asyncio
async def test_short_deposit_records_what_was_approved():
    """Asked 1,165, customer sent 1,000, merchant approved: the deposit is
    1,000, not the required amount checkout stamped on the order."""
    order = _order(OrderStatus.PENDING_DEPOSIT)
    order.deposit_required_cents = 116_500
    order.deposit_amount_cents = 116_500
    _, _, txn, _ = await _approve(order, _intent(order, 116_500), declared=100_000)

    assert order.deposit_amount_cents == 100_000
    assert order.deposit_required_cents == 116_500
    assert txn.amount_cents == 100_000

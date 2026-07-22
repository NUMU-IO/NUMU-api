"""Refund creation guard rails.

The case that motivated this file: processing a partial refund moves the order
to ``PARTIALLY_REFUNDED``, and ``CreateRefundUseCase`` used to gate on
``order.is_paid`` — which is ``PAID`` only. A merchant could therefore issue
exactly ONE partial refund per order and was then permanently locked out of the
remaining balance with "Cannot refund an unpaid order". The over-refund
protection further down the use case was unreachable for follow-up refunds.

These tests pin both halves of the contract: a follow-up partial refund is
allowed, and it still cannot exceed what is actually left.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.dto.refund import CreateRefundDTO
from src.application.use_cases.refunds.create_refund import CreateRefundUseCase
from src.core.entities.order import (
    Order,
    OrderShippingAddress,
    PaymentStatus,
)
from src.core.exceptions import ValidationError

ORDER_TOTAL = 27_000  # cents


def _order(payment_status: PaymentStatus, store_id) -> Order:
    return Order(
        id=uuid4(),
        store_id=store_id,
        customer_id=uuid4(),
        order_number="ORD-000039",
        shipping_address=OrderShippingAddress(
            first_name="Yara",
            last_name="Hassan",
            address_line1="1 Nile St",
            city="Cairo",
            country="EG",
        ),
        payment_status=payment_status,
        total=ORDER_TOTAL,
        currency="EGP",
    )


def _use_case(order: Order, owner_id, already_refunded: int):
    refund_repo = AsyncMock()
    refund_repo.get_total_refunded_for_order.return_value = already_refunded
    refund_repo.get_next_refund_number.return_value = "RFD-0004"
    refund_repo.create.side_effect = lambda r: r

    order_repo = AsyncMock()
    order_repo.get_by_id.return_value = order

    store_repo = AsyncMock()
    store_repo.get_by_id.return_value = type(
        "S", (), {"owner_id": owner_id, "id": order.store_id}
    )()
    return CreateRefundUseCase(refund_repo, order_repo, store_repo)


def _dto(order: Order, amount: int) -> CreateRefundDTO:
    return CreateRefundDTO(
        order_id=order.id,
        refund_type="partial",
        reason="customer_request",
        amount=amount,
    )


class TestSequentialPartialRefunds:
    @pytest.mark.asyncio
    async def test_second_partial_refund_is_allowed(self):
        """The regression: PARTIALLY_REFUNDED must not read as "unpaid"."""
        owner_id, store_id = uuid4(), uuid4()
        order = _order(PaymentStatus.PARTIALLY_REFUNDED, store_id)
        use_case = _use_case(order, owner_id, already_refunded=1_000)

        result = await use_case.execute(_dto(order, 1_000), store_id, owner_id)

        assert result.amount == 1_000

    @pytest.mark.asyncio
    async def test_second_partial_refund_still_capped_at_the_balance(self):
        """Widening the gate must not weaken the over-refund check."""
        owner_id, store_id = uuid4(), uuid4()
        order = _order(PaymentStatus.PARTIALLY_REFUNDED, store_id)
        use_case = _use_case(order, owner_id, already_refunded=1_000)

        with pytest.raises(ValidationError, match="exceeds refundable amount"):
            await use_case.execute(_dto(order, ORDER_TOTAL), store_id, owner_id)

    @pytest.mark.asyncio
    async def test_fully_refunded_order_is_rejected(self):
        owner_id, store_id = uuid4(), uuid4()
        order = _order(PaymentStatus.PARTIALLY_REFUNDED, store_id)
        use_case = _use_case(order, owner_id, already_refunded=ORDER_TOTAL)

        with pytest.raises(ValidationError, match="already been fully refunded"):
            await use_case.execute(_dto(order, 500), store_id, owner_id)

    @pytest.mark.asyncio
    async def test_genuinely_unpaid_order_is_still_rejected(self):
        """The original guard's real job — don't refund money never taken."""
        owner_id, store_id = uuid4(), uuid4()
        order = _order(PaymentStatus.PENDING, store_id)
        use_case = _use_case(order, owner_id, already_refunded=0)

        with pytest.raises(ValidationError, match="unpaid order"):
            await use_case.execute(_dto(order, 500), store_id, owner_id)

    @pytest.mark.asyncio
    async def test_paid_order_first_refund_unaffected(self):
        owner_id, store_id = uuid4(), uuid4()
        order = _order(PaymentStatus.PAID, store_id)
        use_case = _use_case(order, owner_id, already_refunded=0)

        result = await use_case.execute(_dto(order, 1_000), store_id, owner_id)

        assert result.amount == 1_000


class TestOrderRefundableProperty:
    def test_is_refundable_covers_paid_and_partially_refunded(self):
        store_id = uuid4()
        assert _order(PaymentStatus.PAID, store_id).is_refundable
        assert _order(PaymentStatus.PARTIALLY_REFUNDED, store_id).is_refundable

    def test_is_refundable_excludes_unpaid_states(self):
        store_id = uuid4()
        for status in (
            PaymentStatus.PENDING,
            PaymentStatus.FAILED,
            PaymentStatus.REFUNDED,
        ):
            assert not _order(status, store_id).is_refundable, status

"""Unit tests for order status transitions and entity behaviour."""

from datetime import datetime
from uuid import uuid4

import pytest

from src.core.entities.order import (
    FulfillmentStatus,
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _addr():
    return OrderShippingAddress(
        first_name="Test",
        last_name="User",
        address_line1="1 Main St",
        city="Cairo",
        country="EG",
    )


def _order(status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING, **kw):
    defaults = dict(
        id=uuid4(),
        store_id=uuid4(),
        customer_id=uuid4(),
        order_number=f"ORD-{uuid4().hex[:6].upper()}",
        shipping_address=_addr(),
        status=status,
        payment_status=payment_status,
        subtotal=10000,
        total=10000,
        currency="EGP",
    )
    defaults.update(kw)
    return Order(**defaults)


# ---------------------------------------------------------------------------
# Status transition: happy paths
# ---------------------------------------------------------------------------

class TestOrderStatusTransitions:
    """Verify valid status transitions on the Order entity."""

    def test_pending_to_confirmed(self):
        o = _order()
        o.confirm()
        assert o.status == OrderStatus.CONFIRMED

    def test_confirmed_to_processing(self):
        o = _order(status=OrderStatus.CONFIRMED)
        o.start_processing()
        assert o.status == OrderStatus.PROCESSING

    def test_processing_to_shipped(self):
        o = _order(status=OrderStatus.PROCESSING)
        o.ship(tracking_number="TRK-001")
        assert o.status == OrderStatus.SHIPPED
        assert o.tracking_number == "TRK-001"
        assert o.shipped_at is not None
        assert o.fulfillment_status == FulfillmentStatus.FULFILLED

    def test_shipped_to_delivered(self):
        o = _order(status=OrderStatus.SHIPPED)
        o.deliver()
        assert o.status == OrderStatus.DELIVERED
        assert o.delivered_at is not None

    def test_mark_as_paid(self):
        o = _order()
        o.mark_as_paid(payment_id="pay_abc", payment_method="paymob")
        assert o.payment_status == PaymentStatus.PAID
        assert o.status == OrderStatus.PROCESSING
        assert o.payment_id == "pay_abc"
        assert o.payment_method == "paymob"
        assert o.paid_at is not None

    def test_mark_payment_failed(self):
        o = _order()
        o.mark_payment_failed(reason="Insufficient funds")
        assert o.payment_status == PaymentStatus.FAILED
        assert o.metadata["payment_failure_reason"] == "Insufficient funds"

    def test_cancel_pending_order(self):
        o = _order()
        o.cancel(reason="Changed mind")
        assert o.status == OrderStatus.CANCELLED
        assert o.cancelled_at is not None
        assert o.metadata["cancellation_reason"] == "Changed mind"

    def test_cancel_confirmed_order(self):
        o = _order(status=OrderStatus.CONFIRMED)
        o.cancel()
        assert o.status == OrderStatus.CANCELLED

    def test_refund_delivered_paid_order(self):
        o = _order(status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PAID)
        o.refund(reason="Defective item")
        assert o.status == OrderStatus.REFUNDED
        assert o.payment_status == PaymentStatus.REFUNDED

    def test_partial_refund(self):
        o = _order(payment_status=PaymentStatus.PAID)
        o.partial_refund(amount=5000, reason="Partial return")
        assert o.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert o.metadata["partial_refund_amount"] == 5000


# ---------------------------------------------------------------------------
# Status transition: invalid / guard rails
# ---------------------------------------------------------------------------

class TestOrderInvalidTransitions:
    """Verify that invalid transitions raise errors."""

    def test_cannot_confirm_non_pending(self):
        o = _order(status=OrderStatus.CONFIRMED)
        with pytest.raises(ValueError, match="Cannot confirm"):
            o.confirm()

    def test_cannot_ship_pending_order(self):
        o = _order(status=OrderStatus.PENDING)
        with pytest.raises(ValueError, match="Cannot ship"):
            o.ship()

    def test_cannot_deliver_non_shipped(self):
        o = _order(status=OrderStatus.PROCESSING)
        with pytest.raises(ValueError, match="Cannot deliver"):
            o.deliver()

    def test_cannot_cancel_shipped_order(self):
        o = _order(status=OrderStatus.SHIPPED)
        assert not o.can_be_cancelled
        with pytest.raises(ValueError, match="Cannot cancel"):
            o.cancel()

    def test_cannot_refund_unpaid_order(self):
        o = _order(status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PENDING)
        assert not o.can_be_refunded
        with pytest.raises(ValueError, match="cannot be refunded"):
            o.refund()

    def test_cannot_partial_refund_unpaid(self):
        o = _order(payment_status=PaymentStatus.PENDING)
        with pytest.raises(ValueError, match="Cannot refund unpaid"):
            o.partial_refund(amount=1000)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestOrderProperties:
    """Verify computed properties on the Order entity."""

    def test_is_paid(self):
        o = _order(payment_status=PaymentStatus.PAID)
        assert o.is_paid

    def test_is_not_paid(self):
        o = _order(payment_status=PaymentStatus.PENDING)
        assert not o.is_paid

    def test_is_fulfilled(self):
        o = _order()
        o.fulfillment_status = FulfillmentStatus.FULFILLED
        assert o.is_fulfilled

    def test_item_count(self):
        items = [
            OrderLineItem(
                product_id=uuid4(),
                product_name="A",
                quantity=3,
                unit_price=100,
                total_price=300,
            ),
            OrderLineItem(
                product_id=uuid4(),
                product_name="B",
                quantity=2,
                unit_price=200,
                total_price=400,
            ),
        ]
        o = _order(line_items=items)
        assert o.item_count == 5

    def test_can_be_cancelled_pending(self):
        assert _order(status=OrderStatus.PENDING).can_be_cancelled

    def test_can_be_cancelled_confirmed(self):
        assert _order(status=OrderStatus.CONFIRMED).can_be_cancelled

    def test_cannot_be_cancelled_processing(self):
        assert not _order(status=OrderStatus.PROCESSING).can_be_cancelled

    def test_can_be_refunded(self):
        o = _order(status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PAID)
        assert o.can_be_refunded

    def test_cannot_be_refunded_if_not_delivered(self):
        o = _order(status=OrderStatus.SHIPPED, payment_status=PaymentStatus.PAID)
        assert not o.can_be_refunded


# ---------------------------------------------------------------------------
# Utility methods
# ---------------------------------------------------------------------------

class TestOrderUtilities:
    """Tests for add_note, update_tracking, etc."""

    def test_add_note_first_time(self):
        o = _order()
        o.add_note("First note")
        assert o.notes == "First note"

    def test_add_note_appends(self):
        o = _order(notes="Existing note")
        o.add_note("Second note")
        assert "Existing note" in o.notes
        assert "Second note" in o.notes

    def test_update_tracking(self):
        o = _order()
        o.update_tracking("TRK-999", "https://track.example.com/TRK-999")
        assert o.tracking_number == "TRK-999"
        assert o.tracking_url == "https://track.example.com/TRK-999"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestOrderSchemas:
    """Tests for order Pydantic request/response schemas."""

    def test_create_order_request_requires_line_items(self):
        from pydantic import ValidationError
        from src.api.v1.schemas.tenant.order import CreateOrderRequest

        with pytest.raises(ValidationError):
            CreateOrderRequest(
                customer_id=uuid4(),
                line_items=[],
                shipping_address={
                    "first_name": "A",
                    "last_name": "B",
                    "address_line1": "1 St",
                    "city": "Cairo",
                    "country": "EG",
                },
            )

    def test_update_order_status_request_requires_status(self):
        from pydantic import ValidationError
        from src.api.v1.schemas.tenant.order import UpdateOrderStatusRequest

        with pytest.raises(ValidationError):
            UpdateOrderStatusRequest()

    def test_bulk_update_request_max_100(self):
        from pydantic import ValidationError
        from src.api.v1.schemas.tenant.order import BulkUpdateOrderStatusRequest

        with pytest.raises(ValidationError):
            BulkUpdateOrderStatusRequest(
                order_ids=[uuid4() for _ in range(101)],
                status="confirmed",
            )

    def test_bulk_update_request_valid(self):
        from src.api.v1.schemas.tenant.order import BulkUpdateOrderStatusRequest

        req = BulkUpdateOrderStatusRequest(
            order_ids=[uuid4(), uuid4()],
            status="confirmed",
        )
        assert len(req.order_ids) == 2
        assert req.status == "confirmed"

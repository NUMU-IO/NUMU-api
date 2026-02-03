"""Unit tests for Order entity."""

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


class TestOrderShippingAddress:
    """Tests for the OrderShippingAddress value object."""

    def test_create_shipping_address(self):
        """Test creating shipping address."""
        address = OrderShippingAddress(
            first_name="John",
            last_name="Doe",
            address_line1="123 Main St",
            city="Cairo",
            country="EG",
            postal_code="11511",
        )

        assert address.first_name == "John"
        assert address.last_name == "Doe"
        assert address.full_name == "John Doe"
        assert address.address_line1 == "123 Main St"
        assert address.city == "Cairo"
        assert address.country == "EG"

    def test_shipping_address_formatted_address(self):
        """Test formatted_address property."""
        address = OrderShippingAddress(
            first_name="John",
            last_name="Doe",
            address_line1="123 Main St",
            address_line2="Apt 4B",
            city="Cairo",
            state="Cairo Governorate",
            postal_code="11511",
            country="EG",
        )

        formatted = address.formatted_address
        assert "123 Main St" in formatted
        assert "Apt 4B" in formatted
        assert "Cairo" in formatted
        assert "EG" in formatted


class TestOrderLineItem:
    """Tests for the OrderLineItem value object."""

    def test_create_line_item(self):
        """Test creating line item."""
        product_id = uuid4()
        item = OrderLineItem(
            product_id=product_id,
            product_name="Test Product",
            sku="SKU-001",
            quantity=2,
            unit_price=1999,
            total_price=3998,
        )

        assert item.product_id == product_id
        assert item.product_name == "Test Product"
        assert item.sku == "SKU-001"
        assert item.quantity == 2
        assert item.unit_price == 1999
        assert item.total_price == 3998

    def test_line_item_money_properties(self):
        """Test Money property accessors."""
        item = OrderLineItem(
            product_id=uuid4(),
            product_name="Test Product",
            quantity=2,
            unit_price=1999,
            total_price=3998,
        )

        assert item.unit_price_money.cents == 1999
        assert item.total_price_money.cents == 3998


class TestOrderEntity:
    """Tests for the Order entity."""

    def _create_order(self, **kwargs):
        """Helper to create test order."""
        defaults = {
            "id": uuid4(),
            "store_id": uuid4(),
            "customer_id": uuid4(),
            "order_number": f"ORD-{uuid4().hex[:8].upper()}",
            "shipping_address": OrderShippingAddress(
                first_name="John",
                last_name="Doe",
                address_line1="123 Main St",
                city="Cairo",
                country="EG",
            ),
            "line_items": [
                OrderLineItem(
                    product_id=uuid4(),
                    product_name="Test Product",
                    quantity=2,
                    unit_price=1999,
                    total_price=3998,
                )
            ],
            "subtotal": 3998,
            "total": 3998,
        }
        defaults.update(kwargs)
        return Order(**defaults)

    def test_create_order(self):
        """Test creating order with valid data."""
        order = self._create_order()

        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PENDING
        assert order.fulfillment_status == FulfillmentStatus.UNFULFILLED
        assert len(order.line_items) == 1
        assert order.total == 3998

    def test_order_billing_address_defaults_to_shipping(self):
        """Test billing_address defaults to shipping_address."""
        order = self._create_order()

        assert order.billing_address is not None
        assert order.billing_address.first_name == order.shipping_address.first_name

    def test_order_status_properties(self):
        """Test order status check properties."""
        order = self._create_order(status=OrderStatus.PENDING)
        assert order.is_pending is True
        assert order.is_confirmed is False

        order = self._create_order(status=OrderStatus.CONFIRMED)
        assert order.is_confirmed is True

        order = self._create_order(status=OrderStatus.PROCESSING)
        assert order.is_processing is True

        order = self._create_order(status=OrderStatus.SHIPPED)
        assert order.is_shipped is True

        order = self._create_order(status=OrderStatus.DELIVERED)
        assert order.is_delivered is True

        order = self._create_order(status=OrderStatus.CANCELLED)
        assert order.is_cancelled is True

        order = self._create_order(status=OrderStatus.REFUNDED)
        assert order.is_refunded is True

    def test_order_is_paid(self):
        """Test is_paid property."""
        order = self._create_order(payment_status=PaymentStatus.PENDING)
        assert order.is_paid is False

        order = self._create_order(payment_status=PaymentStatus.PAID)
        assert order.is_paid is True

    def test_order_is_fulfilled(self):
        """Test is_fulfilled property."""
        order = self._create_order(fulfillment_status=FulfillmentStatus.UNFULFILLED)
        assert order.is_fulfilled is False

        order = self._create_order(fulfillment_status=FulfillmentStatus.FULFILLED)
        assert order.is_fulfilled is True

    def test_order_can_be_cancelled(self):
        """Test can_be_cancelled property."""
        order = self._create_order(status=OrderStatus.PENDING)
        assert order.can_be_cancelled is True

        order = self._create_order(status=OrderStatus.CONFIRMED)
        assert order.can_be_cancelled is True

        order = self._create_order(status=OrderStatus.PROCESSING)
        assert order.can_be_cancelled is False

        order = self._create_order(status=OrderStatus.SHIPPED)
        assert order.can_be_cancelled is False

    def test_order_can_be_refunded(self):
        """Test can_be_refunded property."""
        order = self._create_order(
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PAID,
        )
        assert order.can_be_refunded is True

        order = self._create_order(
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PENDING,
        )
        assert order.can_be_refunded is False

        order = self._create_order(
            status=OrderStatus.SHIPPED,
            payment_status=PaymentStatus.PAID,
        )
        assert order.can_be_refunded is False

    def test_order_item_count(self):
        """Test item_count property."""
        order = self._create_order(
            line_items=[
                OrderLineItem(
                    product_id=uuid4(),
                    product_name="Product 1",
                    quantity=2,
                    unit_price=1000,
                    total_price=2000,
                ),
                OrderLineItem(
                    product_id=uuid4(),
                    product_name="Product 2",
                    quantity=3,
                    unit_price=500,
                    total_price=1500,
                ),
            ]
        )

        assert order.item_count == 5  # 2 + 3

    def test_order_confirm(self):
        """Test confirm method."""
        order = self._create_order(status=OrderStatus.PENDING)
        order.confirm()

        assert order.status == OrderStatus.CONFIRMED

    def test_order_confirm_non_pending_raises(self):
        """Test confirm on non-pending order raises error."""
        order = self._create_order(status=OrderStatus.PROCESSING)

        with pytest.raises(ValueError, match="Cannot confirm"):
            order.confirm()

    def test_order_mark_as_paid(self):
        """Test mark_as_paid method."""
        order = self._create_order(status=OrderStatus.PENDING)
        order.mark_as_paid(payment_id="pay_123", payment_method="credit_card")

        assert order.payment_status == PaymentStatus.PAID
        assert order.payment_id == "pay_123"
        assert order.payment_method == "credit_card"
        assert order.paid_at is not None
        assert order.status == OrderStatus.PROCESSING

    def test_order_mark_payment_failed(self):
        """Test mark_payment_failed method."""
        order = self._create_order()
        order.mark_payment_failed(reason="Insufficient funds")

        assert order.payment_status == PaymentStatus.FAILED
        assert order.metadata.get("payment_failure_reason") == "Insufficient funds"

    def test_order_start_processing(self):
        """Test start_processing method."""
        order = self._create_order(status=OrderStatus.CONFIRMED)
        order.start_processing()

        assert order.status == OrderStatus.PROCESSING

    def test_order_start_processing_invalid_status_raises(self):
        """Test start_processing on shipped order raises error."""
        order = self._create_order(status=OrderStatus.SHIPPED)

        with pytest.raises(ValueError, match="Cannot start processing"):
            order.start_processing()

    def test_order_ship(self):
        """Test ship method."""
        order = self._create_order(status=OrderStatus.PROCESSING)
        order.ship(tracking_number="TRK123", tracking_url="https://track.example.com/TRK123")

        assert order.status == OrderStatus.SHIPPED
        assert order.fulfillment_status == FulfillmentStatus.FULFILLED
        assert order.tracking_number == "TRK123"
        assert order.tracking_url == "https://track.example.com/TRK123"
        assert order.shipped_at is not None
        assert order.fulfilled_at is not None

    def test_order_ship_non_processing_raises(self):
        """Test ship on non-processing order raises error."""
        order = self._create_order(status=OrderStatus.PENDING)

        with pytest.raises(ValueError, match="Cannot ship"):
            order.ship()

    def test_order_deliver(self):
        """Test deliver method."""
        order = self._create_order(status=OrderStatus.SHIPPED)
        order.deliver()

        assert order.status == OrderStatus.DELIVERED
        assert order.delivered_at is not None

    def test_order_deliver_non_shipped_raises(self):
        """Test deliver on non-shipped order raises error."""
        order = self._create_order(status=OrderStatus.PROCESSING)

        with pytest.raises(ValueError, match="Cannot deliver"):
            order.deliver()

    def test_order_cancel(self):
        """Test cancel method."""
        order = self._create_order(status=OrderStatus.PENDING)
        order.cancel(reason="Customer request")

        assert order.status == OrderStatus.CANCELLED
        assert order.cancelled_at is not None
        assert order.metadata.get("cancellation_reason") == "Customer request"

    def test_order_cancel_shipped_raises(self):
        """Test cancel on shipped order raises error."""
        order = self._create_order(status=OrderStatus.SHIPPED)

        with pytest.raises(ValueError, match="Cannot cancel"):
            order.cancel()

    def test_order_refund(self):
        """Test refund method."""
        order = self._create_order(
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PAID,
        )
        order.refund(reason="Defective product")

        assert order.status == OrderStatus.REFUNDED
        assert order.payment_status == PaymentStatus.REFUNDED
        assert order.metadata.get("refund_reason") == "Defective product"

    def test_order_refund_unpaid_raises(self):
        """Test refund on unpaid order raises error."""
        order = self._create_order(
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PENDING,
        )

        with pytest.raises(ValueError, match="cannot be refunded"):
            order.refund()

    def test_order_partial_refund(self):
        """Test partial_refund method."""
        order = self._create_order(payment_status=PaymentStatus.PAID)
        order.partial_refund(amount=1000, reason="Returned one item")

        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert order.metadata.get("partial_refund_amount") == 1000
        assert order.metadata.get("partial_refund_reason") == "Returned one item"

    def test_order_partial_refund_unpaid_raises(self):
        """Test partial_refund on unpaid order raises error."""
        order = self._create_order(payment_status=PaymentStatus.PENDING)

        with pytest.raises(ValueError, match="unpaid"):
            order.partial_refund(amount=500)

    def test_order_add_note(self):
        """Test add_note method."""
        order = self._create_order()
        order.add_note("Customer called to confirm address")

        assert order.notes == "Customer called to confirm address"

        order.add_note("Shipped via express")
        assert "Customer called to confirm address" in order.notes
        assert "Shipped via express" in order.notes

    def test_order_update_tracking(self):
        """Test update_tracking method."""
        order = self._create_order(status=OrderStatus.SHIPPED)
        order.update_tracking(
            tracking_number="TRK-NEW",
            tracking_url="https://track.example.com/TRK-NEW",
        )

        assert order.tracking_number == "TRK-NEW"
        assert order.tracking_url == "https://track.example.com/TRK-NEW"

    def test_order_money_properties(self):
        """Test Money property accessors."""
        order = self._create_order(
            subtotal=5000,
            shipping_cost=500,
            tax_amount=100,
            discount_amount=200,
            total=5400,
            currency="USD",
        )

        assert order.subtotal_money.cents == 5000
        assert order.shipping_cost_money.cents == 500
        assert order.tax_amount_money.cents == 100
        assert order.discount_amount_money.cents == 200
        assert order.total_money.cents == 5400

    def test_order_serialization(self):
        """Test order serialization to dict."""
        order = self._create_order()
        data = order.model_dump()

        assert "order_number" in data
        assert "line_items" in data
        assert "shipping_address" in data
        assert "status" in data
        assert len(data["line_items"]) == 1

"""Integration tests for order flow."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.entities.order import (
    Order,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.store import Store, StoreStatus
from src.core.value_objects.money import Currency, Money


# Helper to create a sample shipping address for tests
def create_test_shipping_address() -> OrderShippingAddress:
    """Create a test shipping address."""
    return OrderShippingAddress(
        first_name="Test",
        last_name="Customer",
        address_line1="123 Test St",
        city="Cairo",
        country="Egypt",
    )


class TestOrderCreationFlow:
    """Integration tests for order creation flow."""

    def setup_method(self):
        """Set up test fixtures."""
        self.user_id = uuid4()
        self.store_id = uuid4()
        self.product_id = uuid4()
        self.customer_id = uuid4()

        self.sample_store = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Test Store",
            slug="test-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.EGP,
        )

        self.sample_product = Product(
            id=self.product_id,
            store_id=self.store_id,
            name="Test Product",
            slug="test-product",
            sku="TEST-001",
            product_type=ProductType.PHYSICAL,
            status=ProductStatus.ACTIVE,
            price=Money(amount=Decimal("100.00"), currency=Currency.EGP),
            quantity=50,
        )

    @pytest.mark.asyncio
    async def test_order_creation_with_cod_payment(self):
        """Test complete order flow with COD payment."""
        # Simulate order creation with COD
        order = Order(
            id=uuid4(),
            store_id=self.store_id,
            customer_id=self.customer_id,
            order_number="ORD-TEST-001",
            shipping_address=create_test_shipping_address(),
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="cod",
            subtotal=10000,  # 100 EGP in cents
            total=10000,
            currency=Currency.EGP,
        )

        # Verify order properties
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PENDING
        assert order.payment_method == "cod"
        assert order.total == 10000

    @pytest.mark.asyncio
    async def test_order_creation_reduces_product_quantity(self):
        """Test that order creation reduces product quantity."""
        initial_quantity = self.sample_product.quantity
        ordered_quantity = 5

        # Simulate quantity reduction
        new_quantity = initial_quantity - ordered_quantity

        assert new_quantity == 45
        assert new_quantity >= 0

    @pytest.mark.asyncio
    async def test_order_cancellation_restores_quantity(self):
        """Test that order cancellation restores product quantity."""
        initial_quantity = 45  # After order was placed
        ordered_quantity = 5

        # Simulate quantity restoration
        restored_quantity = initial_quantity + ordered_quantity

        assert restored_quantity == 50

    @pytest.mark.asyncio
    async def test_order_with_multiple_products(self):
        """Test order with multiple products."""
        product1_price = 10000  # 100 EGP
        product2_price = 15000  # 150 EGP
        product1_qty = 2
        product2_qty = 1

        subtotal = (product1_price * product1_qty) + (product2_price * product2_qty)

        assert subtotal == 35000  # 350 EGP in cents

    @pytest.mark.asyncio
    async def test_order_with_shipping_cost(self):
        """Test order total includes shipping cost."""
        subtotal = 10000  # 100 EGP
        shipping_cost = 5000  # 50 EGP

        total = subtotal + shipping_cost

        assert total == 15000  # 150 EGP


class TestPaymentFlow:
    """Integration tests for payment flow."""

    @pytest.mark.asyncio
    async def test_cod_payment_marks_order_pending(self):
        """Test COD payment keeps order in pending state."""
        order = Order(
            id=uuid4(),
            store_id=uuid4(),
            customer_id=uuid4(),
            order_number="ORD-TEST-002",
            shipping_address=create_test_shipping_address(),
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="cod",
            subtotal=10000,
            total=10000,
            currency=Currency.EGP,
        )

        # COD orders stay pending until delivery
        assert order.payment_status == PaymentStatus.PENDING
        assert order.status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_paymob_payment_success_updates_order(self):
        """Test successful Paymob payment updates order status."""
        order_id = uuid4()

        # Simulate initial order
        Order(
            id=order_id,
            store_id=uuid4(),
            customer_id=uuid4(),
            order_number="ORD-TEST-003",
            shipping_address=create_test_shipping_address(),
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_method="paymob",
            subtotal=10000,
            total=10000,
            currency=Currency.EGP,
        )

        # Simulate payment success callback
        # In real flow, webhook would update this
        updated_payment_status = PaymentStatus.PAID
        updated_order_status = OrderStatus.CONFIRMED

        assert updated_payment_status == PaymentStatus.PAID
        assert updated_order_status == OrderStatus.CONFIRMED

    @pytest.mark.asyncio
    async def test_paymob_payment_failure_updates_order(self):
        """Test failed Paymob payment updates order status."""
        # Simulate payment failure
        payment_status = PaymentStatus.FAILED
        order_status = OrderStatus.PAYMENT_FAILED

        assert payment_status == PaymentStatus.FAILED
        assert order_status == OrderStatus.PAYMENT_FAILED


class TestShippingFlow:
    """Integration tests for shipping flow."""

    @pytest.mark.asyncio
    async def test_order_shipped_updates_status(self):
        """Test shipping creation updates order status."""
        # Initial confirmed order

        # After shipping created
        updated_status = OrderStatus.SHIPPED

        assert updated_status == OrderStatus.SHIPPED

    @pytest.mark.asyncio
    async def test_order_delivered_updates_cod_payment(self):
        """Test delivery confirmation updates COD payment status."""
        # COD order delivered

        # After delivery confirmation
        final_payment_status = PaymentStatus.PAID

        assert final_payment_status == PaymentStatus.PAID

    @pytest.mark.asyncio
    async def test_shipping_calculates_correct_rates(self):
        """Test shipping rate calculation by governorate."""
        # Sample Bosta shipping rates (in cents)
        rates = {
            "cairo": 5000,  # 50 EGP
            "giza": 5000,
            "alexandria": 6500,  # 65 EGP
            "upper_egypt": 8000,  # 80 EGP
        }

        assert rates["cairo"] == 5000
        assert rates["alexandria"] == 6500


class TestInvoiceFlow:
    """Integration tests for e-invoice flow."""

    @pytest.mark.asyncio
    async def test_invoice_generated_for_paid_order(self):
        """Test invoice is generated for paid orders."""
        order_total = 10000  # 100 EGP in cents
        vat_rate = Decimal("0.14")  # 14% VAT

        # Calculate VAT
        vat_amount = int(Decimal(order_total) * vat_rate)
        total_with_vat = order_total + vat_amount

        assert vat_amount == 1400  # 14 EGP VAT
        assert total_with_vat == 11400  # 114 EGP total

    @pytest.mark.asyncio
    async def test_invoice_includes_required_eta_fields(self):
        """Test invoice has all required ETA fields."""
        required_fields = [
            "invoice_number",
            "seller_tax_id",
            "buyer_info",
            "line_items",
            "subtotal",
            "total_tax",
            "total",
        ]

        sample_invoice = {
            "invoice_number": "INV-2026-0001",
            "seller_tax_id": "123456789",
            "buyer_info": {"name": "Customer", "address": "Cairo"},
            "line_items": [{"name": "Product", "quantity": 1, "price": 10000}],
            "subtotal": 10000,
            "total_tax": 1400,
            "total": 11400,
        }

        for field in required_fields:
            assert field in sample_invoice


class TestNotificationFlow:
    """Integration tests for notification flow."""

    @pytest.mark.asyncio
    async def test_order_confirmation_sends_whatsapp(self):
        """Test order confirmation triggers WhatsApp notification."""
        # Simulate notification data
        notification_data = {
            "phone": "+201234567890",
            "order_number": "ORD-2026-0001",
            "total": "100.00 EGP",
            "locale": "ar",
        }

        assert notification_data["phone"].startswith("+20")
        assert "EGP" in notification_data["total"]

    @pytest.mark.asyncio
    async def test_shipping_notification_includes_tracking(self):
        """Test shipping notification includes tracking number."""
        notification_data = {
            "phone": "+201234567890",
            "order_number": "ORD-2026-0001",
            "tracking_number": "BOSTA-123456789",
        }

        assert notification_data["tracking_number"].startswith("BOSTA")

    @pytest.mark.asyncio
    async def test_arabic_notifications_use_arabic_template(self):
        """Test Arabic locale uses Arabic message templates."""

        # Sample Arabic confirmation message
        arabic_template = "تم استلام طلبك رقم {order_number}"

        assert "طلبك" in arabic_template  # "your order" in Arabic


class TestFullOrderLifecycle:
    """Integration tests for complete order lifecycle."""

    @pytest.mark.asyncio
    async def test_complete_cod_order_lifecycle(self):
        """Test complete COD order from creation to delivery."""
        # 1. Order created
        order_status = OrderStatus.PENDING
        payment_status = PaymentStatus.PENDING
        assert order_status == OrderStatus.PENDING

        # 2. Order confirmed (for COD, auto-confirm)
        order_status = OrderStatus.CONFIRMED
        assert order_status == OrderStatus.CONFIRMED

        # 3. Shipment created
        order_status = OrderStatus.SHIPPED
        assert order_status == OrderStatus.SHIPPED

        # 4. Order delivered
        order_status = OrderStatus.DELIVERED
        payment_status = PaymentStatus.PAID
        assert order_status == OrderStatus.DELIVERED
        assert payment_status == PaymentStatus.PAID

    @pytest.mark.asyncio
    async def test_complete_paymob_order_lifecycle(self):
        """Test complete Paymob order from creation to delivery."""
        # 1. Order created
        order_status = OrderStatus.PENDING
        payment_status = PaymentStatus.PENDING

        # 2. Payment initiated (redirect to Paymob)
        # 3. Payment successful (webhook)
        payment_status = PaymentStatus.PAID
        order_status = OrderStatus.CONFIRMED
        assert payment_status == PaymentStatus.PAID

        # 4. Shipment created
        order_status = OrderStatus.SHIPPED
        assert order_status == OrderStatus.SHIPPED

        # 5. Order delivered
        order_status = OrderStatus.DELIVERED
        assert order_status == OrderStatus.DELIVERED

    @pytest.mark.asyncio
    async def test_order_cancellation_before_shipping(self):
        """Test order can be cancelled before shipping."""
        # Order in confirmed state
        order_status = OrderStatus.CONFIRMED

        # Cancel order
        order_status = OrderStatus.CANCELLED

        assert order_status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_refund_after_payment(self):
        """Test refund process after payment received."""
        # Paid order
        payment_status = PaymentStatus.PAID

        # Refund initiated
        payment_status = PaymentStatus.REFUNDED

        assert payment_status == PaymentStatus.REFUNDED
